# NAVI 1.0.18 室内障碍提醒

本次针对办公室走廊误报“坑洼、路灯”的问题，按“场景过滤 → 室内模型 → 短时确认 → 部署测试”的顺序调整。手机仍使用后置 RGB 摄像头，GPU 执行 Swin-L / Mask2Former。此功能提供物体提醒，不提供距离、通行安全判断或自动绕行。

## 本次修改

1. 默认采用室内类别白名单，室外类别及墙、地面等结构不触发障碍播报。保留本帧模型原始分类、得分和过滤原因，避免仅隐藏误报却无法诊断。
2. Swin-L 架构保留，检查点切换为官方 ADE20K 语义分割模型。支持行人、椅子、办公椅、桌子、办公桌、箱子、柜子、垃圾桶、盆栽、立柱、门、楼梯、台阶等。原 Mapillary 权重保留供回退和显式室外配置使用；不是双模型常驻运行。
3. 普通物体需相邻两帧确认：同类别、框交并比至少 0.25、画面面积比至少 0.5、水平中心位移不超过画面宽度的 0.15；两次采样间隔超过 2 秒或中间缺失则重置。中央、大面积且高分的行人直接提醒，避免等待第二帧。大面积是图像占比，不是距离。
4. 像素内部得分至少 0.65；过滤小碎片，行人最小面积为画面 0.4%，其他物体为 0.8%。得分不是校准后的正确概率，不能将“0.9”解释为“90%正确”。
5. 同时最多一个避障请求，无旧帧队列；结果超过拍摄后 3 秒失效。暂停、断网、关闭和切换会话会清空确认状态。持续相同提醒按 6 秒限频；到达提示仍优先。
6. 门仅提示观察开闭状态，楼梯/台阶提示停下确认；不会仅凭类别宣布门已关闭或判断上下楼方向。

普通用户与管理员复用同一导航页面。独立 NAVI 测试包：1.0.18 / versionCode 19，沿用原包名与测试签名。RoboGuide 的 navi_indoor 模块同步源码；本次交付的是独立 NAVI 升级包，没有重新构建 RoboGuide 原生宿主 APK。

## 手机测试

1. 覆盖安装工作区中的 NAVI-中文版-1.0.18-室内避障测试.apk，不必卸载旧版。继续使用原来已经连通的视觉服务和数据库配置，避障复用视觉服务的 /obstacles 接口，不增加第三个地址。
2. 开始原有导航后，打开“室内避障（实验）”开关。
3. 先在空走廊持稳手机，观察是否仍出现路灯/坑洼播报，再用可见的椅子、箱子和静止行人逐个测试。普通物体要允许连续两个有效画面；突然出现的大面积高分行人可以首帧提醒。
4. 测试门与楼梯时观察提示措辞，站在平地拍摄即可。此版不测距，也不能代替对实际环境的判断。
5. 切换左右朝向，检查提示相对摄像头画面的左/中/右是否一致；再断网、恢复、停止和重启导航，检查旧提醒是否被清除。
6. 遇到误报、漏报或一直“候选1/2”，点避障栏的“识别诊断”信息按钮，复制最近30帧记录。记录含原始类别、过滤原因、候选框、面积、得分、确认状态、服务处理和画面年龄；不保存照片。导航诊断也会节流记录避障事件。

## 模型与部署

- 模型：facebook/mask2former-swin-large-ade-semantic。
- 固定 revision：aa25c92404a40599614215e76514c79b427c7527。
- 模型卡与许可：https://huggingface.co/facebook/mask2former-swin-large-ade-semantic 。保留上游许可要求；权重不随 Git 或 APK 分发。
- model-indoor-manifest.json 固定配置与权重的大小和 SHA-256；check_model.py 校验所有文件。
- GPU 根目录：/home/seaway/sdb/niulinqiang/roboguide_demo；继续使用既有 navi-obstacle-env 和 DINO 指定的 GPU UUID，不修改 DINO/CPU 环境依赖。
- 正式接口保持环回 8003；既有 DINO 网关通过 /obstacles/health 与 /obstacles/analyze 转发。候选验证使用环回 18003，不开放额外公网端口。
- 本次部署先在 navi-obstacle-indoor-stage 验证，再由 deploy_indoor.py 切换。正式 model-indoor 链接到该目录下的已校验模型，部署后需保留该目录，不能把它当临时目录删除。
- 旧源码备份在 navi-obstacle/deploy-backups/；原 model/ 和 model-manifest.json 保留。脚本核对进程命令行后才停止自有避障服务，失败会恢复源码。
- manage_obstacle.sh 与既有 cron 继续维持服务，部署标记防止切换时重复拉起；会识别僵尸进程，避免把退出残留误认为服务仍运行。
- NAVI_OBSTACLE_PROFILE 默认 indoor。outdoor 仅供显式研发回退；新版手机协议会拒绝室外或旧版服务输出，防止模型/类别混用。

新机器需在独立的避障 Python 环境中准备 PyTorch CUDA、Transformers、FastAPI、Uvicorn、python-multipart、Pillow、NumPy、SciPy 和 requests；不要直接修改既有 DINO 环境。当前管理脚本中的 GPU UUID 读取路径和服务器根目录是部署机器配置，迁移时需调整。

在本目录、已安装 huggingface_hub 的环境中，按清单下载室内模型并校验：

```bash
python -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='facebook/mask2former-swin-large-ade-semantic', revision='aa25c92404a40599614215e76514c79b427c7527', allow_patterns=['config.json', 'preprocessor_config.json', 'model.safetensors'], local_dir='model-indoor')"
python check_model.py
```

`download_model.py` 为历史 Mapillary 室外模型下载脚本，不用于上述室内模型。校验通过后，在选定单张 GPU 的环境中启动 `obstacle_server.py`；该进程默认只监听环回端口 8003，由视觉网关转发。

复现策略测试：在此目录运行 python -m unittest -v test_obstacle_policy test_deployment；需安装 NumPy、SciPy、requests。GPU 候选环境先运行 check_model.py，再启动服务，使用 verify_indoor.py --port 18003 验证。旧 verify_remote.py / verify_concurrency.py 对应 11月12日的室外模型验证，不用于当前室内模型验收。

## 验证范围（2026-11-16）

- Flutter 全套93项测试通过；包含新增普通物体两帧确认、位置/面积/类别变化、缺帧及间隔重置、行人首帧、旧协议拒绝、诊断失败隔离和诊断界面测试。
- Python 策略11项、部署防误停与僵尸进程判断2项通过。
- 公开厨房图识别到行人、桌子、柜子；公开房间图识别到椅子；公开公交图识别到行人。灰色空白画面无候选。
- 也保留了猫遮挡沙发的近景失败样例：该图未检出沙发，原始分类落在低分的其他类别；记录保留在 verification-indoor-18003.json，不能据几个正例声称模型不会漏检。
- 候选服务器小样本并行请求：避障130–172 ms；DINO热请求30–54 ms，768维特征最大差小于1e-5。DINO本轮首个冷请求3261 ms。这是服务器内环回测量，手机网络耗时需另测。
- 正式部署及最终健康检查见本次交付验证记录 validation-indoor.json。APK签名、对齐校验通过，签名与原版一致。
- 一加 Ace 6T 在实际走廊中的误报率、漏检率和端到端提示延迟仍待复测；没有用户走廊录像，不能把图片和规则测试等同于实地通过。透明玻璃、低矮障碍、反光、遮挡和弱光仍可能漏检。

公开图片来源（仅供重现，不提交图片到源码仓库）：

- 厨房：http://images.cocodataset.org/val2017/000000397133.jpg
- 椅子：http://images.cocodataset.org/val2017/000000000139.jpg
- 猫/沙发：https://huggingface.co/datasets/huggingface/documentation-images/resolve/main/coco_sample.png
- 行人：https://raw.githubusercontent.com/ultralytics/assets/main/im/bus.jpg
