# 剪映 macOS 11.5.3 本地适配

本地分支 `codex/jianying-11.5.3`，上游基线 `bb1e72cfc2bbed71ba7587548cebc85330b3e29a`。这是本机验证的适配，不能称为全部功能完美兼容。

## 已验证范围

| 功能 | 结果与证据（仓库内 work 目录） |
| --- | --- |
| Codec、版本、签名、库身份 | 中文/emoji 编解码；未知版本、库或 codec 变化拒绝 |
| 基础多轨、剪切、恒定变速、旋转画中画、字幕及音频 | `ui-acceptance-1153` 原生打开、播放、保存、冷重开；`export-1153-final` 原生 MP4 |
| 六类线性关键帧 | x/y/scale/rotation/opacity/volume；`advanced-1153/keyframe-pixel-audio.json` 测量位置、面积、亮度和音量变化 |
| 六种静态蒙版 | circle/rectangle/line/mirror/star/heart；`advanced-1153/masks.png` 六种实际输出逐一检视 |
| 叠化转场 | `advanced-1153/dissolve-export`；红蓝过渡像素测量见 `visual-metrics.json` |
| 轻微抖动 | `advanced-1153/shake-export`；与无效果基线差异见 `shake-pixel-diff.json` |
| 黄色复古立体字 | 新资源 `text-effect/yellow-retro`；剪映 UI 本机导出成功，`advanced-1153/yellow-export` 无界面输出检视通过 |
| 离线组合草稿、组合内编辑 | 单层和两层嵌套输出可见；`advanced-1153/recursive-final-export` 保留三层时间线，字幕与画中画正常 |
| 综合效果 | `advanced-smoke-ubxrh9vf` 正常 pinned CLI，6 秒 180 帧 H.264/AAC，蒙版、关键帧、叠化、抖动和花字同时使用 |
| 较长片段音画同步 | `advanced-1153/sync-30s-export` 30 秒 900 帧；首尾闪光/提示音对齐测量见 `sync-analysis.json`，未观察到新增漂移 |

“关键帧支持”仅指原仓库的线性通道；“效果支持”仅指核对过身份、参数与原始资源 hash 的项目，不泛指整个剪映素材库。字体/资源需在本机存在；资源许可仍由提供方规定。本机可导出不构成商业使用授权。

## 持久化与末帧的后续修复

旧版并未解决这两项：上游 `require_publishable` 明确拒绝 11.4.2 组合登记，理由就是保存丢目录；旧导出校验也允许一帧误差。资源代码只是记录作者本机成功样本，并不提供账号权益。

### 单层组合保存：已找到条件性修复

反汇编定位到 `libVECreator` 的两个加载分支：

- 新异步 `SubDraftLoader::loadSubDraftBasicInfo` 位于 `0x1e34384`，没有设置 project ID。
- 同步 `SubDraftManagerImpl::LoadSubDraftBasicInfo` 位于 `0x1e5b5ec`，调用 `SetProjectId`。
- `sub_draft_async_load_enable` 位于 `0xda0e64`，读取性能开关 `draft_load_optimize_part1.sub_draft_async_load`。

关闭此性能优化后，单层组合使用同步分支，第二次冷重开保存保留目录编号。正式入口又验证了组合内改字幕、创建副本、发布及两轮 UI 保存，记录为 `single-after-save-1.json`、`single-after-save-2.json`，原生导出 `single-final-export` 为 60/60 帧。`work/persistence-frame-1153/after-save-1.json` 和 `after-save-2.json` 均通过严格验证，未放松目录校验。正式入口新增 `ui-compat`，仅修改这一布尔值，调用已校验 hash 的应用 MMKV 库更新数据及 CRC，并留下私有备份和恢复凭据。未改应用二进制、授权、账号或其他设置。

**使用条件：每次完全启动剪映前，先退出剪映并执行 `ui-compat`。** 远端配置刷新会重新开启此优化；不能把一次准备当作永久修复。发布入口要求单层完整 sidecar 和当前关闭的异步开关。双层及更深组合仍不能登记 UI：界面加载器丢失孙级子草稿，`nested-ui-save.json` 证明其消失；无界面导出则已另行递归修复。

```sh
python3 skills/yichen-jianying-edit/scripts/headless_draft.py ui-compat --out /absolute/work/new-ui-preparation
# 然后启动剪映，打开单层组合；后续每次冷启动重复准备。
python3 skills/yichen-jianying-edit/scripts/headless_draft.py ui-compat --check
# 需要恢复原性能开关时，先退出剪映：
python3 skills/yichen-jianying-edit/scripts/headless_draft.py ui-compat --out /absolute/work/new-restore --restore-from /absolute/work/new-ui-preparation/result.json
```

`--check` 使用数据库副本，不改活动数据库；检查也要求剪映关闭。准备命令不会自动启动应用或修改已有项目。已经损坏的旧测试草稿不会被静默修复；请从完整快照重新构建。

### 末帧：严格验收与有界原生重试

逐帧比较确认 59 帧样本缺的是最后一帧，前 53 帧像素完全一致，之后只有微小编码差异；不是错位或中间掉帧。原生异步完成回调仍不能完全排除该问题。

11.5.3 正式导出现在只接受精确帧数。仅对“少最后一帧”最多启动三次独立原生导出；相同输入、相同时间线，不复制帧、不补黑帧、不外部重编码。完整结果才成为 `render.mp4`；失败尝试和日志保留，持续失败返回错误并将输出标为 `partial-render.mp4`。超过一帧、增加帧数、内容图改变和其他错误不会盲目重试。每次尝试有独立超时，最多三倍单次导出耗时。

`work/persistence-frame-1153/exact-repeat.json` 记录 8 次连续测试均最终 60/60 帧；第 6 次首次 59 帧、原生重试后 60 帧，证明重试分支实际被触发。报告包含 `frame_exact` 和 `native_attempts`。这是适配器层保证成功产物完整，并非声称已经修复剪映内部编码器。

### 仍未解决的范围

- 两层以上组合的 UI 编辑持久化仍被阻止；两层无界面导出已验证。
- 高清黑白滤镜本机可预览，但原生导出要求会员；旧橙色花字也未取得新版导出验证。保持授权门槛，不以改字段、补丁或独立调用替代账号权益。新增黄色复古花字是单独的已验证资源。
- 其他第三方特效、云草稿、AI 功能以及任意真实项目不在此次验收范围。当前无旧版安装，未重新实测 11.4.2。

## 原理与修改

导出在独立沙箱进程中加载签名应用的 `libvideoeditor.dylib`，创建 Lyra session，依次执行 ProjectService init、DraftService draftInit、restoreDraft、ExportService exportStart。剪映原生引擎负责渲染和 MP4 写入；ffmpeg 仅用于合成测试输入、解码、抽帧与测量。

| ABI 项目 | 11.4.2 | 11.5.3 |
| --- | --- | --- |
| restoreDraft facade | `0x21234d0` | `0x21b9388` |
| ExportStart 构造函数 | `0x2681f98` | `0x2759398` |
| ToVeCompileSetting | `0x3b7805c` | `0x3c6b4f4` |
| 请求大小 / config 起点 | `0x3d8` / `0x60` | 相同 |
| 宽/高/fps/码率/MP4 writer | `0x3f/0x43/0x4a/0x5e/0x279` | 相同 |
| 恢复/编译完成日志行 | `669/1203` | 相同；额外等待异步完成行 `1300` |

蒙版配置路径槽位 `0x7e8` 经新版反汇编和六种渲染复核后启用，只指向签名应用内置资源。资源清单保留旧版，新增单独 hash 固定的 11.5.3 补充清单；允许的两份 Metal 缓存文件亦核对精确 hash。

11.5.3 的 `GetDraftFromJson` 在本次两层组合中丢掉更深的子时间线，早期探针虽然生成可解码 MP4，画面却全黑。现在为每个子时间线生成独立、校验 hash 的导出输入，递归调用 `DraftQuery::getAllMaterialDraft` / `MaterialDraft::set_draft` 补全，再进行恢复。导出后额外核对完整时间线图、轨道和片段顺序、素材引用及时间范围；丢子草稿的输出会被拒绝。图深度/数量维持上游限制；本次视觉验收只覆盖两层嵌套，不宣称所有深度均验证。

UI 保存格式从 `185.0.0` 迁移为 `187.0.0`，只在 11.5.3 profile 接受；默认声道映射空值转 `none` 等差异只作窄范围比较归一化。源素材与原草稿不被覆盖。独立 codec SHA-256 为 `0c84d54ef5e1abbd72243c96c6a441da1e1a50441b7e539da3fb63c24f53b734`，引擎库 SHA-256 为 `9ac52035d017977eb27cf51b9468847e28cce951153502659881636598f81101`。

## 运行与验收

```sh
python3 tools/build_native_codec.py
python3 skills/yichen-jianying-edit/scripts/headless_draft.py doctor
python3 tools/probe_codec.py
python3 tools/smoke_1153_advanced.py
python3 -m unittest discover -s tests -v
python3 tools/check_package.py
```

综合 smoke 创建新的私有 `work/advanced-smoke-*` 目录，不登记首页；需先在剪映下载对应已验证资源。导出仍用原入口：

```sh
python3 skills/yichen-jianying-edit/scripts/headless_draft.py export --build /absolute/work/new-build --out /absolute/work/new-export
```

输出 `render.mp4`、`result.json`、运行时时间线、ffprobe 和原生日志。它导出经过核验的 build 快照，不包含尚未纳入快照的 UI 修改。修改源草稿后须重建快照；不会静默忽略源文件变化。

本轮 29 项包/版本/递归及条件发布回归测试和 28 项原生导出校验测试通过。源码包与 pins 校验通过。实际内容另以抽帧、像素和音频测量验收，不能以单元测试或“MP4 可解码”替代视觉验收。

本分支不分发官方库、资源或编译二进制；原仓库及第三方许可继续适用。
