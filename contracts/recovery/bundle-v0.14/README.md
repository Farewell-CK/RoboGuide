# Recovery profile v0.14：私有接收 Archive 退休

显式 `roboguide.recovery-profile/v0.14` 延续 v0.13/bundle v0.4 的全部原
Controller/Replica/Memory/Artifact writer、identity、目录、原 journal 和 bytes
验证；未退休的 source Artifact 仍为 storage v3，shared wrapper 保持 v50。

此扩展只接受原 command 私有接收 Archive 的 terminal storage v4。该格式永久
拒绝普通读/写，不能当作新的可写 v4 CAS。完整原 retirement intent、实际原 OS
管理主体/保留原因/原 kernel budget、writer/process evidence 和 exact sealed
owner/receipt 均 mandatory。已知 consumer、其它 Snapshot/Archive pin、pending
source copy、缺 bytes 或原 publication/compact receipt 不能退休。

命令是原 OS administrator 离线维护入口，所有原 writer 排他锁先于读取：

```sh
PYTHONPATH=apps/recovery-tool uv run python -m roboguide_recovery retire-archive \
  --profile <explicit-v0.14-profile> --controller <original-controller-component> \
  --archive-command <original-copy-id> --original-intent-digest <exact-sha256> \
  --command-id <original-retirement-id> --reason <explicit-retention-decision>
PYTHONPATH=apps/recovery-tool uv run python -m roboguide_recovery gc-archive \
  --profile <same-explicit-v0.14-profile> --controller <original-controller-component> \
  --archive-command <original-copy-id> --original-intent-digest <exact-sha256> \
  --command-id <original-gc-id> --reason <explicit-reclamation-decision>
```

retire 不删除 bytes。GC 的独立原 intent、实际当前 writer/process fence、输入
inode 和原 budget 先持久化；retirement tombstone/全部版本 seal 先于 unlink。
旧 Unknown command 重试只观察，不重复效果；另一个新显式 GC 可以处理剩余
原 bytes，不能替旧命令捏造完成。原 head 不得丢失已知命令，原历史/receipt 和
partial 文件保留。restore receipt v0.14 保留这些证据但不启动服务。

只对完整退休的接收 history 允许已删除 bytes。缺 tombstone/head/原 journal
或 owner seal，甚至重新计算 manifest 也拒绝；未退休原引用仍必须核对实际 bytes。
旧 profile/CLI/普通 v3 binary 拒绝终态 owner，不能重新赋予读权或复制。

该路径不关闭源 Artifact 全引用 GC、Raft/online、多数派 ACK、跨域恢复或完整
SoftwareComplete；所有设备与物理停止证据独立保留。
