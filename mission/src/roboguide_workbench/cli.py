"""Terminal entry point for scoped queries and explicit Mission planning and submission."""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

from mission.config import current_environment

from roboguide_workbench.actions import MissionActions
from roboguide_workbench.client import ReadOnlyController, WorkbenchError
from roboguide_workbench.configuration import apply_profile, explicit_option, session_scope
from roboguide_workbench.conversation import ConversationSession
from roboguide_workbench.display import DisplayPersistenceError, DisplayTurn, discard_confirmation
from roboguide_workbench.persistence import SessionStore
from roboguide_workbench.provider_config import load_chat_settings
from roboguide_workbench.routing import ResponsesRouter
from roboguide_workbench.session import Session
from roboguide_workbench.views import safe_text
from roboguide_workbench.workspace import WorkbenchSession


def parser() -> argparse.ArgumentParser:
    """Declare CLI inputs without opening connections or reading model credentials."""
    root = argparse.ArgumentParser(
        prog="roboguide", description="RoboGuide 对话工作台", allow_abbrev=False
    )
    commands = root.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("chat", "交互对话或单次提问"),
        ("nodes", "集群节点概览"),
        ("node", "指定节点详情"),
        ("capabilities", "按能力或合同检索"),
        ("mission", "指定 Mission/Task 状态"),
        ("missions", "当前任务列表"),
        ("doctor", "检查连接与配置"),
        ("init", "配置全局工作台，之后任意目录可直接启动"),
    ):
        command = commands.add_parser(name, help=help_text, allow_abbrev=False)
        command.add_argument("--profile", type=Path, help="独立工作台 TOML 配置")
        command.add_argument("--controller", default="http://127.0.0.1:8080")
        command.add_argument("--tenant", help="原服务端验证的 tenant scope")
        command.add_argument("--timeout", type=float, default=5, help="Controller 请求秒数，默认 5")
        command.add_argument("--ca", type=Path)
        command.add_argument("--client-cert", type=Path)
        command.add_argument("--client-key", type=Path)
        if name in {"chat", "missions", "doctor", "init"}:
            command.add_argument("--mission-service", default="http://127.0.0.1:8070")
            command.add_argument("--mission-timeout", type=float, default=60)
            command.add_argument("--mission-ca", type=Path)
            command.add_argument("--mission-client-cert", type=Path)
            command.add_argument("--mission-client-key", type=Path)
            command.add_argument("--session-dir", type=Path, default=Path("var/workbench/sessions"))
            selection = command.add_mutually_exclusive_group()
            selection.add_argument("--resume", help="恢复指定会话；对象状态将重新查询")
            selection.add_argument("--session", help="指定新会话名，默认自动生成")
        if name in {"chat", "doctor", "init"}:
            command.add_argument("--config", type=Path, default=Path("config/mission.toml"))
            command.add_argument("--plain", action="store_true")
            command.add_argument("--answer-mode", choices=["grounded", "facts"], default="grounded")
            command.add_argument("--tool-limit", type=int, default=4)
            command.add_argument("--call-limit", type=int, default=3)
            command.add_argument("--turn-seconds", type=float, default=60)
            command.add_argument("--ask", help="执行一个问题后退出；支持 /nodes 等命令")
            if name == "init":
                command.add_argument("--base-url", help="Responses 兼容模型服务地址")
                command.add_argument("--model", help="对话模型名称")
                command.add_argument("--api-key-env", default="OPENAI_API_KEY")
                command.add_argument("--no-auth", action="store_true")
                command.add_argument("--output", type=Path, help="全局工作台配置输出路径")
        elif name in {"node", "mission", "capabilities"}:
            command.add_argument("target")
    return root


def main(argv: list[str] | None = None) -> int:
    """Run queries, preserving failures as nonzero outcomes and Ctrl-C as a local exit."""
    actual_argv = sys.argv[1:] if argv is None else argv
    arguments = parser().parse_args(actual_argv)
    store: SessionStore | None = None
    try:
        if arguments.command == "init":
            from roboguide_workbench.onboarding import interactive_setup, write_configuration

            for name in (
                "profile",
                "config",
                "session",
                "resume",
                "session_dir",
                "plain",
                "answer_mode",
                "tool_limit",
                "call_limit",
                "turn_seconds",
                "ask",
                "timeout",
                "mission_timeout",
            ):
                if explicit_option(actual_argv, name):
                    raise WorkbenchError("roboguide init 不使用 --" + name.replace("_", "-") + "。")
            if arguments.base_url or arguments.model:
                if not arguments.base_url or not arguments.model:
                    raise WorkbenchError("非交互配置需要同时提供 --base-url 和 --model。")
                profile = write_configuration(
                    base_url=arguments.base_url,
                    model=arguments.model,
                    controller=arguments.controller,
                    mission_service=arguments.mission_service,
                    api_key_env=arguments.api_key_env,
                    requires_auth=not arguments.no_auth,
                    config_path=arguments.output,
                    tenant=arguments.tenant,
                    ca=arguments.ca,
                    client_cert=arguments.client_cert,
                    client_key=arguments.client_key,
                    mission_ca=arguments.mission_ca,
                    mission_client_cert=arguments.mission_client_cert,
                    mission_client_key=arguments.mission_client_key,
                )
            elif sys.stdin.isatty():
                profile = interactive_setup(
                    config_path=arguments.output, initial=_setup_inputs(arguments, actual_argv)
                )
            else:
                raise WorkbenchError("请交互运行 roboguide init，或提供 --base-url 和 --model。")
            print("配置已保存：" + str(profile) + "\n现在可运行 roboguide chat。")
            return 0
        apply_profile(arguments, actual_argv)
        if (
            arguments.command == "chat"
            and arguments.ask is None
            and sys.stdin.isatty()
            and not arguments.config.exists()
            and arguments.profile is None
            and not any(a == "--config" or a.startswith("--config=") for a in actual_argv)
        ):
            from roboguide_workbench.onboarding import interactive_setup

            arguments.profile = interactive_setup(initial=_setup_inputs(arguments, actual_argv))
            apply_profile(arguments, actual_argv)
        reader = ReadOnlyController(
            arguments.controller,
            timeout=arguments.timeout,
            tenant=arguments.tenant,
            ca=arguments.ca,
            certificate=arguments.client_cert,
            key=arguments.client_key,
        )

        def router_factory() -> ResponsesRouter:
            """Load the explicit model profile only on the first natural-language question."""
            settings = load_chat_settings(arguments.config)
            return ResponsesRouter(settings, current_environment())

        session: Session = Session(reader, router_factory)
        if arguments.command in {"chat", "missions", "doctor"}:
            mission = ReadOnlyController(
                arguments.mission_service,
                timeout=arguments.mission_timeout,
                tenant=arguments.tenant,
                ca=arguments.mission_ca,
                certificate=arguments.mission_client_cert,
                key=arguments.mission_client_key,
            )
            if arguments.command == "doctor":
                failed = False
                for label, connection in (("Controller", reader), ("Mission Service", mission)):
                    try:
                        print(label + ": " + str(connection._get("/readyz")))
                    except ValueError as error:
                        failed = True
                        print(label + ": " + str(error))
                try:
                    configured = router_factory()
                    print("模型配置有效：" + configured.settings.llm.model + "（尚未调用）")
                except (ValueError, OSError) as error:
                    failed = True
                    print("模型配置不可用：" + str(error))
                return 2 if failed else 0
            session_id = arguments.resume or arguments.session or uuid.uuid4().hex
            store = SessionStore(
                arguments.session_dir, session_id, session_scope(arguments), bool(arguments.resume)
            )
            session = ConversationSession(
                reader, router_factory, store, MissionActions(mission, store)
            )
            if arguments.command == "chat":
                session.answer_mode = arguments.answer_mode
                if session.answer_mode not in {"grounded", "facts"}:
                    raise WorkbenchError("未知 answer_mode。")
                session.tool_limit = arguments.tool_limit
                session.call_limit = arguments.call_limit
                session.turn_seconds = arguments.turn_seconds
                if not (
                    1 <= session.tool_limit <= 4
                    and 1 <= session.call_limit <= 3
                    and 0 < session.turn_seconds <= 60
                ):
                    raise WorkbenchError("查询预算超出范围。")
                session.progress = lambda message: print(
                    "RG · " + message, file=sys.stderr, flush=True
                )
            session_id = store.path.stem
            if arguments.command == "chat":
                session.config_path = arguments.config
                try:
                    session.model_label = load_chat_settings(arguments.config).llm.model
                except (ValueError, OSError):
                    session.model_label = "未配置（运行 roboguide init）"
            if arguments.command == "missions":
                display_question(session, "/missions")
                return 0
        if arguments.command != "chat":
            command = {
                "nodes": "/nodes",
                "node": "/node",
                "mission": "/mission",
                "capabilities": "/capabilities",
            }[arguments.command]
            display_question(
                session,
                command + (" " + arguments.target if hasattr(arguments, "target") else ""),
            )
            return 0
        assert isinstance(session, ConversationSession)
        if arguments.ask is not None:
            print(f"会话：{session_id}；恢复：--resume {session_id}", file=sys.stderr)
            display_question(session, arguments.ask)
            return 0 if session.query_status == "complete" else 2
        print("RoboGuide · 对话工作台 v0.4")
        print(f"会话：{session_id}；恢复：--resume {session_id}")
        print(f"Controller：{safe_text(reader.endpoint)}")
        print(f"tenant：{safe_text(reader.tenant or '由服务端身份确定')}")
        print("查询、规划、执行共用此会话；/help 查看命令，/quit 退出。")
        if arguments.resume:
            print(
                "已恢复历史：Request="
                + str(session.request_id)
                + "；Mission="
                + str(session.last_mission)
            )
            print("状态将重新查询，执行前需重新展示当前计划。")
        if not arguments.plain and sys.stdin.isatty():
            from roboguide_workbench.terminal import interactive as asynchronous_terminal

            return asyncio.run(asynchronous_terminal(session))
        return interactive(session)
    except (WorkbenchError, ValueError, OSError) as error:
        print("查询失败：" + safe_text(str(error)), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n已退出；已发送操作不会因此撤销。", file=sys.stderr)
        return 130
    finally:
        if store is not None:
            store.close()


def _setup_inputs(arguments: argparse.Namespace, argv: list[str]) -> dict[str, object]:
    """Carry only explicit connection and identity options unchanged into first-run setup."""
    values = {
        name: getattr(arguments, name)
        for name in (
            "controller",
            "mission_service",
            "tenant",
            "api_key_env",
            "ca",
            "client_cert",
            "client_key",
            "mission_ca",
            "mission_client_cert",
            "mission_client_key",
        )
        if hasattr(arguments, name) and explicit_option(argv, name)
    }
    if hasattr(arguments, "no_auth") and explicit_option(argv, "no_auth"):
        values["requires_auth"] = not arguments.no_auth
    return values


def interactive(session: Session) -> int:
    """Read bounded turns until EOF or explicit exit; a failed turn keeps the shell usable."""
    try:
        import readline

        commands = [
            "/nodes",
            "/node",
            "/missions",
            "/mission",
            "/compare",
            "/explain",
            "/watch",
            "/plan",
            "/run",
            "/request",
            "/answer",
            "/answer-to",
            "/execute",
            "/approve",
            "/choose",
            "/observe",
            "/reconcile",
            "/history",
            "/help",
            "/raw",
            "/reset",
            "/quit",
            "/status",
            "/sessions",
            "/new",
            "/resume",
            "/check",
            "/cancel",
            "/feedback",
        ]

        def complete(text: str, state: int) -> str | None:
            """Complete only documented commands without reading files or external inventory."""
            matches = [command for command in commands if command.startswith(text)]
            return matches[state] + " " if state < len(matches) else None

        readline.set_completer(complete)
        readline.set_completer_delims(" ")
        readline.parse_and_bind("tab: complete")
    except ImportError:
        pass
    try:
        return _plain_interactive(session)
    finally:
        discard_confirmation(session)


def display_question(session: Session, question: str, prefix: str = "") -> None:
    """Acknowledge a plain or one-shot result only after a complete write and actual flush."""
    if isinstance(session, WorkbenchSession):
        from roboguide_workbench.sessions import navigate

        switched = navigate(session, question)
        if switched is not None:
            try:
                print(prefix + switched, flush=True)
            except (ValueError, OSError) as error:
                discard_confirmation(session)
                raise DisplayPersistenceError(
                    "会话已切换但提示未完整显示；请重新启动并明确 --resume 会话名。"
                ) from error
            return
    turn = DisplayTurn(session)
    try:
        result = session.ask(question)
        turn.stage()
        print(prefix + result, flush=True)
        turn.acknowledge()
    finally:
        turn.reject()


def _plain_interactive(session: Session) -> int:
    """Run plain input until exit while retaining only completely displayed references."""
    while True:
        try:
            question = input("\n你 > ").strip()
        except EOFError:
            return 0
        if question in {"/quit", "/exit"}:
            return 0
        if not question:
            continue
        try:
            if (question == "/watch" or question.startswith("/watch ")) and isinstance(
                session, WorkbenchSession
            ):
                session.suggestion = None
                session.pending_cancel = None
                turn = DisplayTurn(session)
                try:
                    session.watch(
                        question.partition(" ")[2].strip(),
                        lambda message: print(message, flush=True),
                    )
                    turn.stage()
                    turn.acknowledge()
                finally:
                    turn.reject()
                continue
            print("RG · 正在处理…", file=sys.stderr, flush=True)
            display_question(session, question, "\nRG > ")
        except DisplayPersistenceError as error:
            print("\nRG > 查询失败：" + safe_text(str(error)), flush=True)
            return 2
        except (WorkbenchError, ValueError, OSError) as error:
            print("\nRG > 查询失败：" + safe_text(str(error)), flush=True)
            print("可使用 /help 中的直接查询命令。", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
