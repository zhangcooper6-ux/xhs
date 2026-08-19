# 小红书爆款笔记复刻 + 去重工具

一个 Python 命令行工具：输入一个小红书爆款笔记链接、抖音视频链接、或本地视频文件，自动**下载素材 → 复刻文案 → 按类型去重 → 小红书风格增强 → 二创叠加 → 字幕处理 → AI 配音 → 背景音乐**。

支持三种输入来源（自动识别）：
- **小红书笔记**（视频 / Live 图 / 图文）：完整支持
- **抖音视频**：解析下载 → 去重 → 转小红书风格
- **本地视频文件**：直接去重 → 风格增强 → 二创（最稳定的抖音/快手段视频处理方案）

- **视频**：调用 ffmpeg 做深层去重（镜像/变速/裁剪/调色/音轨变调/噪点/重编码），并**自动生成剪映（Jianying）草稿**，打开剪映即可二次编辑并导出。
- **Live 图**：静态图走 Pillow 去重（镜像/裁剪/调色/噪点/去 EXIF/重编码），动态短视频走 ffmpeg 去重，并生成剪映草稿。
- **图文**：每张图片走 Pillow 去重，破坏原始哈希。

> ⚠️ 本工具仅用于学习研究。请遵守小红书/抖音用户协议与相关著作权法律，复刻内容请获得原作者授权或用于合规的二创场景。使用者自行承担一切法律责任。

---

## 一、环境要求

| 组件 | 要求 |
|------|------|
| 操作系统 | Windows 10/11（剪映草稿目录自动探测仅支持 Windows） |
| Python | 3.8 及以上（已在 3.10 验证） |
| ffmpeg | **不需要单独安装**，程序通过 `imageio-ffmpeg` 自带完整版 ffmpeg |
| 剪映 | 建议安装「剪映专业版」4.x / 5.x（用于自动生成草稿；未安装也能用，仅产出视频/图片文件） |

### 安装依赖

在 `d:\xhs` 目录下打开终端：

```bash
pip install -r requirements.txt
```

依赖清单：`requests`、`beautifulsoup4`、`lxml`、`Pillow`、`imageio-ffmpeg`（首次安装会下载约 80MB 的 ffmpeg 二进制，请耐心等待）、`edge-tts`（AI 配音）、`opencv-python`（动态水印去除-颜色模式）、`paddleocr`（动态水印去除-OCR 模式，可选）。

---

## 二、快速开始

### 1. 复刻一条视频笔记（去重 + 风格增强 + 剪映草稿）

```bash
python xhs_clone.py "https://www.xiaohongshu.com/explore/你的笔记ID?xsec_token=..."
```

执行后（默认完整流程）：
1. 下载视频到 `output/_downloads/<笔记ID>/`
2. ffmpeg 去重 → `output/dedup/<笔记ID>/video_dedup.mp4`
3. **小红书风格增强** → `output/dedup/<笔记ID>/video_final.mp4`（最终成品）
4. 在剪映草稿目录生成草稿 `XHS视频_<ID>`
5. 文案 → `output/dedup/<笔记ID>/content_clone.txt`

> URL 必须用**英文双引号 `"`** 整体包住（含 `&` 会被 shell 截断）。新版分享链接带 `xsec_token`，必须一并复制进命令行。

### 1a. 复刻抖音视频（转小红书风格）

工具支持自动识别抖音链接并解析下载：

```bash
# 抖音网页 URL
python xhs_clone.py "https://www.douyin.com/video/xxxxxxxx"

# 抖音短链（从 App 分享复制）
python xhs_clone.py "https://v.douyin.com/xxxxxx/"
```

> ⚠️ **抖音 2026 年大幅加强反爬，免登录解析已不稳定**。如果抖音链接解析失败，推荐用以下**最稳定方案**：
>
> 1. 用抖音 App 或微信小程序（如"小青去水印"、"抖抖去水印"）下载无水印视频到本地
> 2. 直接用本地文件路径作为输入：
>    ```bash
>    python xhs_clone.py "下载的视频.mp4" --subtitle-style bold_yellow --watermark "@你的小红书号"
>    ```
> 3. 工具会自动完成：去重 → 小红书风格增强 → 字幕抹除+重渲染 → 二创叠加 → 剪映草稿

### 1b. 直接处理本地视频文件（最通用）

不依赖任何平台解析，直接对本地视频做去重+增强+二创：

```bash
python xhs_clone.py "你的视频.mp4" --watermark "@你的小红书号" --subtitle-style bold_yellow
```

适用场景：
- 抖音/快手下载的视频（自行用去水印小程序下载）
- 自己拍摄的视频
- 已下载的小红书视频

输出到 `output/dedup/local_<文件名>/` 目录，包含 `video_dedup.mp4`（去重版）和 `video_final.mp4`（风格增强版）。

### 2. 风格增强做了什么

让成品更符合小红书爆款调性：

| 维度 | 处理 |
|------|------|
| **尺寸** | 强制 1080x1920（9:16 竖屏），主体居中，背景用高斯模糊填充（不裁主体） |
| **色调** | 暖色滤镜（colorbalance 提红提黄压蓝），偏温馨 |
| **亮度** | 提亮 + 提饱和 + 提伽马，明亮鲜艳（小红书偏好） |
| **质感** | hqdn3d 轻微磨皮 + unsharp 锐化，皮肤柔和画面通透 |
| **背景** | 模糊背景压暗，前景更突出 |
| **过渡** | 片头淡入 0.4s + 片尾淡出 0.4s |
| **水印** | 可选右下角账号水印（`--watermark "@你的小红书号"`） |

### 2b. 自动二创叠加层（默认开启，无需手工剪映）

在风格增强之后，自动叠加以下二创元素，进一步破坏查重并增强视觉效果：

| 叠加层 | 说明 | 默认参数 |
|--------|------|---------|
| **贴纸 overlay** | 从 emoji 贴纸库随机选 1-4 个，半透明叠加到画面四角，带轻微飘动动画 | 2 个，透明度 2%（极淡） |
| **扫光效果** | 白色半透明光条从左到右扫过画面，模拟运镜光效 | 2 次，透明度 5% |
| **字幕条** | 底部半透明黑条 + 居中文字（自动用笔记标题，或 `--caption` 自定义） | 底部，黑条 45% 透明 |
| **噪点纹理** | 极淡随机噪点，破坏像素级查重 | 强度 3（几乎不可见） |
| **画中画** | 叠加另一个视频素材到角落（`--pip other.mp4`） | 默认关闭 |

所有叠加层透明度都可调，默认值极淡（贴纸 2%、扫光 5%），肉眼几乎看不出但能破坏哈希查重。

### 1b-3. 字幕处理（抹除原字幕 + 重新渲染新字幕）

默认开启，自动处理三步：

| 步骤 | 说明 | 默认 |
|------|------|------|
| **自动识别字幕区域** | 抽帧 + 边缘检测 + 水平投影分析，自动找到字幕位置 | 开启 |
| **抹除原字幕** | 用识别到的区域做模糊/修复/覆盖抹掉 | 开启，blur 模糊 |
| **渲染新字幕** | 用笔记文案（或 whisper 识别）生成 ASS 字幕并烧录 | 开启，default 样式 |

**自动识别字幕区域原理**：

无需手动量坐标，工具会自动：
1. 从视频中段（20%~80% 时长）抽取 5 帧（可调 `--detect-frames`）
2. 缩放到 480 宽加速分析
3. 对每帧做灰度化 + 水平边缘差分 → 每行得到"边缘强度"
4. 多帧取平均，找出底部边缘强度高的连续行段（文字行有大量高频边缘）
5. 阈值取最大值的 50%（紧贴字幕峰值边界，不外扩到背景）
6. 合并相邻段、过滤过短段（<8 行），得到精确字幕区域
7. 上下各扩 2% 画面高度，仅够覆盖描边/阴影，不过度外扩

**新字幕位置自动跟随原字幕**：

检测到原字幕区域后，新渲染的字幕会自动定位到原字幕所在位置（alignment + margin_v 动态计算），让替换看起来自然。也可用 `--subtitle-position` 强制指定位置。

**只检测不处理（用于确认）**：

```bash
# 看看自动识别到的字幕区域是多少
python xhs_clone.py --detect-subtitle-only "你的视频.mp4"

# 输出示例：
# ✅ 检测到字幕区域（相对画面比例）:
#    x = 0.000  y = 0.799  w = 1.000  h = 0.067
#    像素坐标（原视频 720x1280）:
#    起点: (0, 1022)  尺寸: 720x85
```

**字幕抹除方式** (`--erase-mode`)：

| 模式 | 适用场景 | 效果 | 速度 |
|------|---------|------|------|
| `blur` | 默认，对大区域字幕条 | 裁出字幕区域→高斯模糊→覆盖回去 | 快 |
| `delogo` | 原字幕是窄条 logo 式 | ffmpeg delogo 修复（对窄条好，整条字幕会模糊扩散） | 快 |
| `cover` | 原字幕有黑底，直接盖掉 | 纯色矩形覆盖（最快，但有色块） | 最快 |
| `inpaint` | **推荐**，无模糊感 | OpenCV TELEA 逐帧智能填充，根据周围像素推算内容 | 中等 |
| `ocr-inpaint` | **最自然**，背景完全保留 | PaddleOCR 识别字幕文字位置，只修复文字像素 | 慢 |

> - `inpaint` 模式：对整个字幕矩形区域做 TELEA 算法修复，无模糊感、过渡自然。需 `opencv-python`
> - `ocr-inpaint` 模式：先用 OCR 识别字幕文字精确位置，只对文字像素做 inpaint，背景完全保留（最无感）。需 `paddleocr`
> - 两种模式都支持 `--inpaint-radius`（修复半径，默认 4）和 `--inpaint-dilate`（mask 膨胀，默认 2）调参
> - 自动识别失败时（视频无字幕/极淡字幕），会回退到 `config.py` 的 `SUBTITLE.erase_area` 默认值（底部 75%-95%）
> - 也可用 `--no-auto-detect-subtitle` 关闭自动识别，强制用默认区域

```bash
# inpaint 模式（推荐，无模糊感）
python xhs_clone.py "URL" --erase-mode inpaint

# ocr-inpaint 模式（最自然，背景完全保留）
python xhs_clone.py "URL" --erase-mode ocr-inpaint --ocr-inpaint-samples 15

# 调整修复半径和膨胀（文字描边粗时加大 dilate）
python xhs_clone.py "URL" --erase-mode inpaint --inpaint-radius 6 --inpaint-dilate 4
```

**新字幕样式预设** (`--subtitle-style`)：

| 预设 | 字体 | 颜色 | 描边 | 适用 |
|------|------|------|------|------|
| `default` | 黑体 56 | 白字 | 黑描边 3px | 通用 |
| `bold_yellow` | 黑体 64 | 黄字 | 黑描边 4px | 爆款强调（醒目） |
| `clean_box` | 黑体 48 | 白字 | 半透明黑底框 | 简洁/教程 |
| `cute_pink` | 黑体 52 | 粉字 | 白描边 | 美妆/可爱风 |
| `top_title` | 黑体 60 | 白字 | 黑描边 | 顶部标题（非底部字幕） |

**字幕文本来源**（`--subtitle-source`）：

| 来源 | 说明 | 错别字 | 适用场景 |
|------|------|--------|---------|
| `auto`（默认） | 智能选择：`--whisper` 启用→whisper，否则→note | 取决于来源 | 通用 |
| `whisper` | Whisper 语音识别原文（贴合口播内容，时间戳精确） | 可能有错别字 | 口播类视频，不在意个别错字 |
| `llm-fix`（推荐） | Whisper 识别 + LLM 纠错（只修错别字，不改语义/句式） | 基本无错别字 | 口播类视频，要求字幕准确 |
| `note` | 用笔记标题+描述分句（无错别字，但可能和口播不完全一致） | 无 | 笔记文案完整、与口播一致时 |

> 💡 **`llm-fix` 纠错模式**：先用 Whisper 识别口播内容，再用 LLM 只修正明显错别字/同音字误识别/专有名词错误，保留原句式和口语风格。需配合 `--llm-provider` 使用，LLM 不可用时自动回退到 whisper 原文本。
>
> 💡 **二创改写优先级**：若同时启用 `--voice-rewrite`（口播二创改写），改写后的文本优先级最高，字幕和配音共用同一份文本，`--subtitle-source` 不生效。

```bash
# 默认：智能选择（whisper 启用时用 whisper，否则用 note）
python xhs_clone.py "URL" --whisper --subtitle-style bold_yellow

# whisper 原识别（可能有错别字，但完全贴合口播）
python xhs_clone.py "URL" --whisper --subtitle-source whisper

# LLM 纠错模式（推荐：whisper 识别 + LLM 修正错别字）
python xhs_clone.py "URL" --whisper --subtitle-source llm-fix --llm-provider deepseek

# 用笔记文案分句（无错别字，但可能和口播不完全一致）
python xhs_clone.py "URL" --subtitle-source note

# 先确认下自动识别到的字幕区域（不影响视频）
python xhs_clone.py --detect-subtitle-only "output/dedup/xxx/video_dedup.mp4"

# 抹除原字幕 + 用 bold_yellow 样式重新渲染（区域自动识别）
python xhs_clone.py "URL" --subtitle-style bold_yellow

# 只抹除原字幕，不渲染新字幕
python xhs_clone.py "URL" --no-subtitle

# 不抹除原字幕（保留原字幕）
python xhs_clone.py "URL" --no-erase-subtitle

# 关闭自动识别，用 config.py 里的默认区域
python xhs_clone.py "URL" --no-auto-detect-subtitle

# 新字幕强制放底部（不跟随原字幕位置）
python xhs_clone.py "URL" --subtitle-position bottom

# 新字幕放顶部
python xhs_clone.py "URL" --subtitle-position top

# 提高识别精度（抽更多帧）
python xhs_clone.py "URL" --detect-frames 8

# 用 whisper 语音识别提取字幕（更精确的时间戳）
python xhs_clone.py "URL" --whisper --whisper-model small

# 用 delogo 修复模式抹除窄条字幕
python xhs_clone.py "URL" --erase-mode delogo
```

### 1b-4. AI 配音（多服务商：edge-tts / 火山引擎 Seed-TTS 2.0）

默认关闭，用 `--voice <别名>` 开启。视频处理完成后，自动用 AI 生成配音并替换/混合原音频。

**支持两个 TTS 服务商**（用 `--tts-provider` 切换）：

| 服务商 | 标识 | 音色数 | 是否免费 | 自然度 | 适用场景 |
|--------|------|--------|---------|--------|---------|
| **edge-tts**（默认） | `edge` | 26 个 | ✅ 免费/无需 API key | 高 | 通用，快速上手 |
| **火山引擎 Seed-TTS 2.0** | `volcengine` | 24 个 | ❌ 需 API key（有免费额度） | 极高（支持情感表达） | 高质量配音、情感丰富内容 |

> 💡 选择火山引擎音色（`bd_` 前缀别名）时，即使不指定 `--tts-provider volcengine`，工具也会自动切换到火山引擎服务商。

#### 服务商 1：edge-tts（默认，免费）

基于微软 Edge 在线神经网络语音合成，**免费、无需 API key、自然度高**。

**26 个热门中文配音**（`--list-voices` 查看全部）：

| 分类 | 配音别名 | 推荐场景 |
|------|---------|---------|
| **普通话女声（11个）** | `xiaoxiao` 晓晓 / `xiaoyi` 晓伊 / `xiaohan` 晓涵 / `xiaomeng` 晓梦 / `xiaomo` 晓墨 / `xiaoqiu` 晓秋 / `xiaorui` 晓睿 / `xiaoshuang` 晓双(儿童) / `xiaoxuan` 晓萱 / `xiaoyan` 晓颜 / `xiaozhen` 晓甄 | 生活/美妆/情感/母婴/读书/种草/汽车/亲子/旅行/美食/剧情 |
| **普通话男声（7个）** | `yunjian` 云健 / `yunxi` 云希 / `yunxia` 云夏 / `yunyang` 云扬 / `yunfeng` 云枫 / `yunhao` 云皓 / `yunze` 云泽 | 知识科普/潮流数码/历史故事/新闻资讯/读书情感/校园/财经 |
| **方言（2个）** | `xiaobei` 晓北(东北) / `xiaoni` 晓妮(陕西) | 搞笑/本地内容 |
| **粤语（3个）** | `hiugaai` 曉佳 / `hiumaan` 曉曼 / `wanlung` 允龍 | 粤语内容 |
| **台湾国语（3个）** | `hsiaochen` 曉臻 / `hsiaoyu` 曉雨 / `yunjhe` 云哲 | 台腔内容 |
| **智能匹配** | `auto` | 根据笔记标签/标题关键词自动选最合适的配音 |

#### 服务商 2：火山引擎 Seed-TTS 2.0（高质量，支持情感表达）

基于字节跳动豆包语音团队 Seed-TTS 2.0 模型，**自然度更高、支持情感表达、能模仿真人说话的细微瑕疵**。在中文语音合成基准测评中以 93.06 分领跑行业。

**配置方法**（仅需 2 步）：

```powershell
# 1. 安装依赖（如未安装）
pip install requests

# 2. 设置环境变量（火山引擎控制台 → 语音合成 → 应用管理 获取）
setx VOLCENGINE_APP_ID "你的APPID"
setx VOLCENGINE_ACCESS_KEY "你的Access Token"
```

> 💡 设置完环境变量后**需重启终端**才生效。也可在命令前临时设置：
> `$env:VOLCENGINE_APP_ID="你的APPID"; $env:VOLCENGINE_ACCESS_KEY="你的Token"; python xhs_clone.py ...`

**24 个热门音色**（`--list-voices` 查看全部，所有别名以 `bd_` 前缀）：

| 分类 | 音色别名 | 推荐场景 |
|------|---------|---------|
| **普通话女声（10个）** | `bd_xiaoyan` 清新女声 / `bd_xiaomei` 湾湾小雀 / `bd_xiaoqian` 温柔小雅 / `bd_xinling` 心灵鸡汤 / `bd_tianxin` 甜美小雨 / `bd_xiaoxia` 少儿故事 / `bd_zhisheng` 知声女童 / `bd_qingqing` 晴晴 / `bd_linjia` 林佳美 / `bd_yueyue` 悦悦 | 生活/美妆/情感/亲子/种草/vlog/时尚 |
| **普通话男声（7个）** | `bd_yunhao` 云豪 / `bd_yunyang` 云扬 / `bd_yunfeng` 云枫 / `bd_yunjian` 云健 / `bd_laohao` 老郝 / `bd_xiaobai` 小白 / `bd_lele` 乐乐 | 知识科普/资讯/读书/解说/历史/数码/校园 |
| **多情感音色（4个）** | `bd_qingxinvsheng` 清新女声 / `bd_qingxinnansheng` 清新男声 / `bd_qiaopi` 俏皮女声 / `bd_wenrou` 温柔女声 | 情感丰富场景/搞笑/治愈 |
| **方言（3个）** | `bd_yueyu` 粤语女声 / `bd_sichuan` 四川话女声 / `bd_dongbei` 东北话男声 | 粤语/四川话/东北话内容 |

> 💡 火山引擎的 `--voice-rate` / `--voice-pitch` / `--voice-volume` 参数会自动从百分比/Hz 格式转换为火山引擎 API 所需的整数格式（speed 范围 [-50, 100]，pitch 范围 [-12, 12]，loudness 范围 [-50, 100]）。

**资源 ID 切换**（可选）：

默认使用 `seed-tts-2.0`（豆包语音合成模型 2.0）。如需使用 1.0 模型，设置环境变量：
```powershell
setx VOLCENGINE_TTS_RESOURCE_ID "seed-tts-1.0"
```

### `--voice auto` 智能匹配

不想手动挑配音？用 `--voice auto`，工具会根据笔记的**标签 + 标题 + 描述**自动匹配最合适的配音。

**匹配规则**：
1. 把标签+标题+描述拼成文本
2. 遍历 17 个场景的关键词表（美妆/母婴/美食/科普/数码/情感/读书/搞笑/新闻/历史/种草/旅行/剧情/亲子/财经/生活等）
3. 统计每个配音命中的关键词数，取命中最多的
4. 全部未命中 → 默认晓晓（最通用）

**匹配示例**：

| 笔记标签/标题 | 自动选择的配音 | 命中关键词 |
|--------------|--------------|-----------|
| `#美食 #探店` 藏在巷子里的宝藏餐厅 | `xiaoyan` 晓颜 | 美食,探店,餐厅,甜品,好吃 |
| `#美妆 #口红试色` 秋冬必入的3支口红 | `xiaoyi` 晓伊 | 美妆,口红 |
| `#科普 #干货` 为什么天空是蓝色的 | `yunjian` 云健 | 科普,原理,干货,讲解 |
| `#数码 #开箱` 苹果新品开箱测评 | `yunxi` 云希 | 数码,新品,测评,开箱 |
| `#搞笑 #段子` 沙雕日常 | `xiaobei` 晓北 | 搞笑,段子,沙雕,笑死,哈哈 |
| `#财经 #股票` 基金投资入门 | `xiaorui` 晓睿 | 财经,股票,基金,理财,投资 |
| `#童话` 睡前故事 | `xiaoshuang` 晓双 | 童话,故事,绘本,儿童 |
| 普通视频（无匹配关键词） | `xiaoxiao` 晓晓 | 默认兜底 |

**配音自然度优化**（默认开启）：
- 自动去除 `#标签`、`@用户`、URL、emoji（避免被读成"井号xxx"）
- 折叠重复标点（`！！！→！`），自动补结尾句号
- 用 `--no-clean-text` 可关闭清理

**音频合成策略**：
- 默认：完全替换原音频
- `--keep-bg-audio`：保留原视频音频作为 BGM（默认降到 15% 音量混合）
- 自动调整配音时长匹配视频时长（atempo 变速不变调，支持多级串联）

**配音时长匹配保护机制**（避免人声变成杂音）：

当配音文本很短而视频很长时（或反过来），极端的变速会让人声失真：
- 配音远短于视频（ratio < 0.3，如 4s 配音 vs 76s 视频）：**不拉伸**，原速播放 + 尾部静音填充
- 配音远长于视频（ratio > 3.0）：**不压缩**，截断到视频时长
- 比例在 0.3~3.0 之间：正常 atempo 变速（保持自然语速）

> 💡 **提示**：如果笔记正文主要是 #标签，清理后只剩标题几句话，配音会很短（只有前几秒有人声，后面静音）。
> 建议用 `--voice-text "更长的配音文本..."` 指定完整解说词，或确保笔记有足够描述文案。

```bash
# 查看全部配音（edge-tts 26 个 + 火山引擎 24 个）
python xhs_clone.py --list-voices

# 自动选配音（根据笔记标签/标题匹配，推荐！）
python xhs_clone.py "URL" --voice auto

# 用晓晓配音（最热门女声，适合生活/好物分享）
python xhs_clone.py "URL" --voice xiaoxiao

# 用云健配音（男声浑厚，适合知识/科普）
python xhs_clone.py "URL" --voice yunjian

# 晓伊配音 + 语速减慢10% + 保留原BGM
python xhs_clone.py "URL" --voice xiaoyi --voice-rate -10% --keep-bg-audio

# 自定义配音文本（默认用笔记标题+描述）
python xhs_clone.py "URL" --voice xiaoxiao --voice-text "自定义配音内容"

# 调音调（偏高更年轻）
python xhs_clone.py "URL" --voice yunxi --voice-pitch +3Hz

# 调音量
python xhs_clone.py "URL" --voice xiaoxiao --voice-volume +15%

# 保留原BGM并调BGM音量（0-1）
python xhs_clone.py "URL" --voice xiaoxiao --keep-bg-audio --bg-volume 0.2

# 关闭配音时长自动匹配（配音可能比视频长或短）
python xhs_clone.py "URL" --voice xiaoxiao --no-fit-duration

# ===== 火山引擎 Seed-TTS 2.0 用法（需先配置 VOLCENGINE_APP_ID / VOLCENGINE_ACCESS_KEY）=====

# 显式指定火山引擎服务商 + 清新女声
python xhs_clone.py "URL" --tts-provider volcengine --voice bd_xiaoyan

# 也可省略 --tts-provider，选择 bd_ 前缀别名时自动切换到火山引擎
python xhs_clone.py "URL" --voice bd_xiaoyan

# 火山引擎 + 浑厚男声 + 语速加快 10%
python xhs_clone.py "URL" --tts-provider volcengine --voice bd_yunhao --voice-rate +10%

# 火山引擎 + 多情感音色（俏皮女声，适合搞笑段子）
python xhs_clone.py "URL" --voice bd_qiaopi

# 火山引擎 + 保留原 BGM
python xhs_clone.py "URL" --voice bd_xiaomei --keep-bg-audio --bg-volume 0.2

# 临时设置环境变量（PowerShell，单次运行）
$env:VOLCENGINE_APP_ID="你的APPID"; $env:VOLCENGINE_ACCESS_KEY="你的Token"; python xhs_clone.py "URL" --voice bd_xiaoyan
```

**配音文本来源**（按优先级）：
1. `--voice-text "文本"`：显式指定（最高优先级）
2. **whisper 识别的字幕**：当 `--whisper` 启用且识别成功时，用识别出的完整字幕文本配音（时长接近视频，避免笔记文案太短导致只有几秒人声）
3. 笔记标题 + 描述（兜底，自动清理后合成）

> 💡 **推荐组合**：`--whisper --subtitle-style bold_yellow --voice auto`
> whisper 识别的字幕同时用于：① 抹除原字幕+重新渲染 ② 作为配音文本。
> 这样配音内容与视频原语音一致，时长匹配，不会出现"几秒人声+大段静音"的问题。
>
> 如果笔记正文主要是 #标签（清理后很短），强烈建议配合 `--whisper` 使用，或用 `--voice-text` 指定完整解说词。

```bash
# 推荐组合：whisper 字幕 + 自动配音（配音用 whisper 识别的完整文本）
python xhs_clone.py "URL" --whisper --whisper-model small --subtitle-style bold_yellow --voice auto

# 只用 whisper 识别文本配音，不渲染字幕
python xhs_clone.py "URL" --whisper --no-subtitle --voice auto

# 显式指定配音文本（优先级最高，覆盖 whisper）
python xhs_clone.py "URL" --whisper --voice auto --voice-text "自定义完整解说词..."
```

### 1b-4b. 口播二创改写（LLM 智能改写原口播文案）

默认关闭，用 `--voice-rewrite` 开启。识别原视频口播内容后，通过 LLM 大模型做二创改写，再用改写后的文案配音。

**适用场景**：基于原带货视频做二创，想换一种表达风格但保留所有关键信息（产品名、卖点、价格等），避免偏离原意。

**工作流程**：
1. `--whisper` 识别原视频口播内容 → 得到原文案
2. `--voice-rewrite` 触发 LLM 按指定风格改写（保留关键信息，不偏离语义）
3. 改写后的文案自动作为 `--voice-text` 来源，用 `--voice` 指定的声音配音

**7 种二创风格**（`--list-rewrite-styles` 查看全部）：

| 风格 | 名称 | 特点 |
|------|------|------|
| `casual` | 口语化 | 更自然口语，像朋友分享（默认） |
| `professional` | 专业带货 | 专业自信，突出卖点 |
| `passion` | 激情推销 | 高能量激情，催促下单 |
| `gentle` | 温柔种草 | 温柔细腻，慢慢安利 |
| `knowledge` | 知识科普 | 理性讲解，原理+好处 |
| `humor` | 幽默段子 | 搞笑段子手风格 |
| `concise` | 精简干练 | 删减冗余，直击要点 |

**硬性约束**（LLM 改写时强制遵守）：
- 必须保留原视频所有关键信息：产品名/品牌/功能/卖点/价格/优惠/使用方法
- 语义不能偏离原意，不能凭空添加原视频没有的信息
- 不编造虚假功效或夸大宣传
- 改写后字数与原文相近（±30%），避免配音时长与视频严重不匹配

```bash
# 查看全部二创风格
python xhs_clone.py --list-rewrite-styles

# 口语化改写（默认风格）
python xhs_clone.py "URL" --whisper --voice auto --voice-rewrite

# 专业带货风格改写
python xhs_clone.py "URL" --whisper --voice auto --voice-rewrite --rewrite-style professional

# 温柔种草风格 + 指定配音
python xhs_clone.py "URL" --whisper --voice xiaoyi --voice-rewrite --rewrite-style gentle

# 激情推销风格 + 滤镜
python xhs_clone.py "URL" --whisper --voice yunyang --voice-rewrite --rewrite-style passion --filter teal_orange
```

> 💡 **说明**：
> - 必须配合 `--whisper`（识别原口播）和 `--voice`（配音）使用，否则二创改写不会触发
> - 若同时指定了 `--voice-text`，则用户指定的文本优先级最高，跳过 LLM 改写
> - LLM 不可用或改写失败时，自动回退到原 whisper 识别文本（不会中断流程）
> - 需先配置 LLM API Key（见「8b. LLM 大模型二创」章节）

### 1b-5. 背景音乐 BGM

默认关闭，用 `--bgm <风格/曲名/auto>` 开启。在视频处理 + 配音完成后，自动混入 BGM 作为背景音。**与配音共存**：配音为主音轨，BGM 为背景（默认 15% 音量）。

**重要：BGM 素材需自备**

工具不内置音乐文件（版权原因），需自行下载放到 `assets/bgm/` 目录。
- 文件名规范：`风格_曲名.mp3`（如 `治愈_起风了.mp3`）
- 工具通过文件名前缀（风格）做自动匹配
- 支持 `.mp3 .wav .m4a .aac .ogg .flac`
- 用 `--list-bgm` 查看 17 个风格共 100+ 首小红书热门 BGM 推荐清单（可按曲名自行搜索下载）

### 下载助手（半自动下载免版税音乐）

为简化 BGM 收集，工具提供 `--download-bgm` 助手：你只需在免版税音乐站浏览、复制直链，填入 `assets/bgm/_sources.json`，脚本自动下载 + 按规范重命名 + 归类。**不批量爬取，不下载版权作品**，只下载你手动收集的合法直链。

**第1步：查看合法免版税音乐来源**
```bash
python xhs_clone.py --list-sources
```
列出推荐站点：Pixabay Music（最推荐，免版税可商用无需署名）/ Bensound / FMA / Musopen 等。

**第2步：浏览音乐、复制直链、填入 _sources.json**

到 Pixabay Music (https://pixabay.com/music/) 等站浏览试听，找到喜欢的曲目，下载并复制浏览器地址栏的直链（形如 `https://cdn.pixabay.com/download/audio/...`），按格式填入 `assets/bgm/_sources.json`：
```json
[
  {"url": "https://cdn.pixabay.com/download/audio/xxx.mp3?filename=xxx.mp3", "style": "治愈", "name": "暖阳"},
  {"url": "https://cdn.pixabay.com/download/audio/yyy.mp3?filename=yyy.mp3", "style": "欢快", "name": "元气"},
  {"url": "", "style": "古风", "name": "留空会被自动跳过"}
]
```
`style` 必须是 17 个风格之一：治愈/欢快/情感/古风/Vlog/氛围/美食/旅行/美妆/卡点/搞笑/动感/浪漫/励志/民谣/电子/说唱。

**第3步：批量下载**
```bash
# 预览将下载的文件，不实际下载
python xhs_clone.py --download-bgm --dry-run

# 实际下载（已存在的会自动跳过）
python xhs_clone.py --download-bgm
```
下载完成后，文件会自动命名为 `治愈_暖阳.mp3` / `欢快_元气.mp3` 放到 `assets/bgm/`，可直接用 `--bgm auto` 或 `--bgm 治愈` 引用。

**17 个 BGM 风格**（每个风格都有推荐曲目）：

| 风格 | 推荐场景 | 示例曲目 |
|------|---------|---------|
| 治愈 | 治愈/暖心/深夜/emo | 起风了、晚安、云烟成雨、大鱼 |
| 欢快 | 欢快/元气/夏日/少女 | 蜜雪冰城甜蜜蜜、卡路里、热爱105°C的你 |
| 情感 | 情感/爱情/失恋/回忆 | 孤勇者、漠河舞厅、错位时空、岁月神偷 |
| 古风 | 古风/汉服/国风/武侠 | 烟雨行舟、红昭愿、锦鲤抄、赤伶、踏山河 |
| Vlog | vlog/日常/记录/生活 | Sunburst、Love Story、小幸运、Perfect |
| 氛围 | 氛围/质感/高级感/大片 | 星空、River Flows in You、卡农、天空之城 |
| 美食 | 美食/探店/做饭/烘焙 | 舌尖上的中国、甜甜的、吃货进行曲 |
| 旅行 | 旅行/旅游/打卡/攻略 | 蓝莲花、去大理、平凡之路、夜空中最亮的星 |
| 美妆 | 美妆/化妆/穿搭/时尚 | Try、Girls、Dance Monkey、Bad Guy |
| 卡点 | 卡点/踩点/混剪/变装 | The Spectre、Alone、Faded、Stronger |
| 搞笑 | 搞笑/段子/沙雕/整活 | 鸡你太美、改革春风吹满地、我的滑板鞋 |
| 动感 | 动感/舞蹈/健身/运动 | Despacito、Shape of You、Uptown Funk |
| 浪漫 | 浪漫/婚礼/求婚/情侣 | 告白气球、甜蜜蜜、月亮代表我的心 |
| 励志 | 励志/奋斗/拼搏/梦想 | 相信自己、追梦赤子心、海阔天空、光辉岁月 |
| 民谣 | 民谣/吉他/弹唱/文艺 | 成都、理想三旬、南山南、董小姐 |
| 电子 | 电子/电音/舞曲/夜店 | Lean On、Waves、Wake Me Up、Animals |
| 说唱 | 说唱/rap/嘻哈/hiphop | 不用去猜、飘向北方、Time、Lifecycle |

**`--bgm auto` 智能匹配**：根据笔记标签+标题关键词自动选 BGM 风格（17 个风格关键词表）。

```bash
# 查看可用 BGM + 100+ 首推荐清单
python xhs_clone.py --list-bgm

# 自动选 BGM（根据笔记标签/标题匹配风格）
python xhs_clone.py "URL" --bgm auto

# 按风格指定
python xhs_clone.py "URL" --bgm 治愈
python xhs_clone.py "URL" --bgm 古风

# 按曲名指定（需文件名包含曲名）
python xhs_clone.py "URL" --bgm 起风了

# 调BGM音量（0-1，默认0.15=15%）
python xhs_clone.py "URL" --bgm 治愈 --bgm-volume 0.2

# 关闭淡入淡出
python xhs_clone.py "URL" --bgm 治愈 --no-bgm-fade

# 配音 + BGM 组合（推荐！配音为主，BGM为背景）
python xhs_clone.py "URL" --voice auto --bgm auto
python xhs_clone.py "URL" --voice xiaoxiao --bgm 治愈 --bgm-volume 0.2
```

**BGM 处理流程**：
1. 扫描 `assets/bgm/` 目录解析可用 BGM（按风格分类）
2. 选择 BGM：`auto`（关键词匹配）/ 风格名 / 曲名
3. BGM 循环或截断到视频时长（`stream_loop`）
4. 自动加淡入（0.5s）+ 淡出（1s）
5. 用 ffmpeg `amix` 混合：原音轨/配音（主）+ BGM（背景，15%）

### 1b-6. 动态水印去除（OCR / 颜色检测 两种模式）

针对视频中**移动的水印**（如"@原作者"飘动文字、平台动态 logo），提供两种去除模式。**默认关闭**。

> 依赖：`pip install opencv-python`（颜色模式）/ `pip install paddleocr`（OCR 模式，推荐）

#### 模式一：OCR 文字检测（推荐，最智能）

用 PaddleOCR 识别画面中所有文字，按**内容**判断哪些是水印（包含 `@`、`原创`、`搬运` 等关键词），只删水印不删字幕。

**原理**：
1. 均匀采样 N 帧（默认 10 帧）运行 OCR
2. 按内容过滤水印文字（`@` 符号 / `--wm-text` 关键词 / 常见水印词）
3. 裁剪水印模板 → 计算水印移动范围（搜索区域）
4. 逐帧用**模板匹配**精确定位水印（毫秒级，快）
5. `cv2.inpaint` 修复水印区域

**优点**：能区分水印和字幕，不会误删字幕；不需要手动指定区域
**缺点**：需安装 paddleocr（约 200MB）；首次运行需下载 OCR 模型

```bash
# 最简单：自动识别 @ 符号水印（推荐）
python xhs_clone.py "URL" --wm-ocr

# 指定水印文字关键词（更精确，多个用逗号分隔）
python xhs_clone.py "URL" --wm-text "@朱西柚,原创"

# 水印移动范围大，增加采样帧数
python xhs_clone.py "URL" --wm-ocr --wm-ocr-samples 20

# 水印变形明显，降低匹配阈值
python xhs_clone.py "URL" --wm-ocr --wm-match-threshold 0.4
```

#### 模式二：颜色检测 + 追踪（默认，无需额外依赖）

用亮度阈值检测水印位置，CSRT 追踪器跟踪移动轨迹。

**优点**：无需额外依赖，速度快
**缺点**：可能误删亮色字幕；追踪会丢失

```bash
# 指定水印起始区域（追踪模式，推荐用颜色模式时指定）
python xhs_clone.py "URL" --remove-wm --wm-region "5%,5%,30%,10%"

# 自动检测（不指定区域，可能误删字幕）
python xhs_clone.py "URL" --remove-wm

# 水印颜色偏灰，降低阈值
python xhs_clone.py "URL" --remove-wm --wm-threshold 180
```

#### 两种模式对比

| 对比项 | OCR 模式（`--wm-ocr`） | 颜色模式（`--remove-wm`） |
|--------|----------------------|------------------------|
| 识别方式 | OCR 识别文字内容 | 亮度阈值检测 |
| 能否区分水印和字幕 | ✅ 能（按内容判断） | ❌ 不能（按亮度，会误删亮色字幕） |
| 是否需要手动指定区域 | ❌ 不需要 | ✅ 建议指定（`--wm-region`） |
| 额外依赖 | paddleocr（约 200MB） | 无（仅需 opencv-python） |
| 处理速度 | 快（OCR 仅采样 10 帧，其余模板匹配） | 中等（追踪快，每帧检测慢） |
| 准确性 | 高（按内容匹配） | 中（颜色相似会误判） |
| **推荐度** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ |

#### 替换为自有水印（两种方式）

把视频中移动的原作者水印**换成自己的账号名**，有两种方式：

| 方式 | 参数 | 效果 | 适用场景 |
|------|------|------|---------|
| **轨迹覆盖**（新） | `--wm-replace "@你的号"` | 去除原水印后，在**原水印位置**覆盖你的文字，**跟随原水印移动轨迹** | 想保持原水印的移动动效 |
| **右下角静态** | `--watermark "@你的号"` | 在画面右下角叠加静态水印 | 传统的固定位置水印 |

> 流程顺序：下载 → 去重 → **去除原水印**（+可选轨迹覆盖）→ 风格增强 → 字幕处理 → 配音。

**方式一：轨迹覆盖（推荐，跟随原水印移动）**

```bash
# 自动识别 @ 符号原水印 → 去除 → 在原位置覆盖你的文字
python xhs_clone.py "URL" --wm-replace "@你的小红书号"

# 精确指定原水印关键词（更准）
python xhs_clone.py "URL" --wm-text "@朱西柚,原创" --wm-replace "@我的账号名"
```

**方式二：右下角静态叠加**

```bash
# 去除原水印 + 右下角叠加你的号
python xhs_clone.py "URL" --wm-ocr --watermark "@你的小红书号"
```

**方式三：两者组合（轨迹覆盖 + 右下角静态，双重水印）**

```bash
python xhs_clone.py "URL" --wm-replace "@你的小红书号" --watermark "@你的小红书号"
```

**颜色模式去除 + 自有水印（无 paddleocr 时）**

```bash
python xhs_clone.py "URL" --remove-wm --wm-region "5%,5%,30%,10%" --watermark "@你的小红书号"
```

**完整组合示例：轨迹覆盖 + 字幕 + 配音**

```bash
python xhs_clone.py "URL" \
  --wm-text "@朱西柚,原创" --wm-replace "@我的小红书号" \
  --subtitle-style bold_yellow \
  --voice auto --bgm 治愈
```

**永久配置默认行为**

编辑 `config.py` 的 `WM_REMOVE` 块可设置默认参数，之后命令行更简洁：

```python
WM_REMOVE = {
    "enabled": True,           # 默认启用去除
    "use_ocr": True,           # 默认用 OCR 模式
    "wm_text": None,           # None=自动识别 @ 符号
    "replace_text": "@你的小红书号",  # 默认沿轨迹覆盖此文字
    # ...其他参数保持默认
}
```

```bash
# config.py 设置默认后，每次只需：
python xhs_clone.py "URL"
```

> 💡 **`--wm-replace` vs `--watermark` 区别**：
> - `--wm-replace`：沿原水印**移动轨迹**覆盖，字体大小自适应原水印，自动启用 OCR 模式
> - `--watermark`：画面右下角**固定位置**静态叠加，字体大小固定
> - 两者可同时使用：轨迹覆盖 + 右下角静态双重水印

#### 完整组合示例

```bash
# 推荐：OCR 去水印 + AI 配音 + BGM（全自动）
python xhs_clone.py "URL" \
  --wm-ocr \
  --voice auto --bgm 治愈_暖阳.mp3

# OCR 去水印 + 抹除原字幕 + 重新渲染字幕 + 配音
python xhs_clone.py "URL" \
  --wm-ocr --wm-text "@原创" \
  --subtitle-style bold_yellow \
  --voice auto

# 只去水印，保留原字幕原音频
python xhs_clone.py "URL" --wm-ocr \
  --no-erase-subtitle --no-subtitle --no-enhance

# 颜色模式：指定区域追踪
python xhs_clone.py "URL" --remove-wm --wm-region "5%,5%,30%,10%"
```

#### 参数调优指南

| 问题 | OCR 模式 | 颜色模式 |
|------|---------|---------|
| 水印没去掉 | `--wm-text` 指定关键词；`--wm-ocr-samples` 加到 20 | `--wm-threshold` 调高（220） |
| 字幕被误删 | （不会发生） | `--wm-threshold` 调低（180）；改用 OCR 模式 |
| 修复区域有痕迹 | `--wm-dilate 2` 减小修复范围 | `--wm-dilate 2` |
| 追踪/匹配失败 | `--wm-match-threshold 0.4` 降低阈值 | `--wm-no-track` 改每帧检测 |
| 处理太慢 | 正常（OCR 仅 10 帧+模板匹配） | 加 `--wm-region`（追踪快很多） |

#### 限制说明
- 水印覆盖复杂背景（如人脸/文字）时，inpaint 修复可能留痕
- 水印颜色与背景完全一致时颜色模式会漏检
- OCR 模式首次运行需下载模型（约 50MB），之后会缓存
- 不影响原音频（ffmpeg 合成时保留原音轨）

### 1b-7. 滤镜与特效（50+ 预设，可配置）

默认关闭，用 `--filter <预设名>` 开启。在风格增强合成之后、字幕处理之前应用，作用于整帧画面，覆盖 7 大分类共 55 个预设：基础调色 / 电影感 / 胶片质感 / 风格化 / 黑白 / 美食风景 / 人像。

**架构**：每个预设 = 原生调色（FFmpeg `eq`/`colorbalance`/`vibrance`/`hqdn3d`） + 可选 LUT（`.cube` 文件，带强度混合） + 特效链（暗角 / 胶片颗粒 / 漏光 / 柔焦 / 故障等）。LUT 与特效素材已内置生成，无需下载。

**查看全部预设**：

```bash
python xhs_clone.py --list-filters
```

输出按分类列出 55 个预设，标注每个预设是否需要 LUT、LUT 是否已就绪、附带哪些特效。例如：

```
【电影感】
  teal_orange          青橙色调       [LUT] +vignette,grain_light
                        经典电影青橙调色
  cinematic            电影感        [LUT] +vignette,grain_light
                        通用电影调色
  ...
共 55 个预设（11 个需要 LUT，11 个 LUT 已就绪）
```

**55 个预设速查表**：

| 分类 | 预设名（--filter 参数） | 中文名 | 适用场景 |
|------|----------------------|--------|---------|
| **基础（10）** | `warm_sunny` `cool_tone` `fresh` `rich` `elegance` `high_contrast` `soft` `vivid` `vintage_warm` `vintage_cool` | 暖阳/冷调/清新/浓郁/淡雅/高对比/柔和/鲜艳/复古暖/复古冷 | 生活日常/科技/春夏/美食风景/家居/街拍/人像/旅行/复古 |
| **电影感（10）** | `teal_orange` `cinematic` `moody_green` `dark_cinema` `orange_cinema` `hollywood` `suspense` `scifi` `war` `noir` | 青橙/电影感/墨绿/暗调电影/橘色电影/好莱坞/悬疑/科幻/战争/黑色电影 | 大片调色/悬疑/科幻/战争/黑白 |
| **胶片（10）** | `kodak_portra` `fuji_400h` `film_grain` `light_leak_film` `faded_film` `polaroid` `film_vignette` `slide_film` `negative_film` `old_photo` | Kodak Portra/Fuji 400H/胶片颗粒/漏光胶片/褪色胶片/宝丽来/胶片暗角/反转片/负片/老照片 | 人像胶片/日系/复古/怀旧 |
| **风格化（10）** | `japanese` `korean` `ins_style` `hongkong` `europe` `cyberpunk` `glitch` `vaporwave` `pastel` `instant` | 日系/韩系/ins风/港风/欧美/赛博朋克/故障/蒸汽波/柔色/拍立得 | 日系/韩系/ins/港风/欧美/赛博/故障/蒸汽波/马卡龙/拍立得 |
| **黑白（5）** | `bw_classic` `bw_high_contrast` `bw_soft` `bw_film` `bw_dark` | 经典黑白/高对比黑白/柔焦黑白/胶片黑白/暗调黑白 | 黑白摄影/纪实/艺术 |
| **美食风景（5）** | `food_vivid` `food_warm` `scenery_bright` `scenery_vivid` `sunset_warm` | 美食鲜艳/美食暖调/风景明亮/风景饱和/日落暖光 | 美食/风景/日落 |
| **人像（5）** | `portrait_soft` `pink_girl` `cool_white` `warm_yellow` `portrait_natural` | 柔肤美白/粉嫩/冷白皮/暖黄皮/自然人像 | 人像/美妆/少女/冷白皮/自然 |

**使用示例**：

```bash
# 查看全部 55 个预设
python xhs_clone.py --list-filters

# 电影青橙调色（最热门电影感预设）
python xhs_clone.py "URL" --filter teal_orange

# Kodak Portra 经典人像胶片
python xhs_clone.py "URL" --filter kodak_portra

# 日系低饱和明亮风
python xhs_clone.py "URL" --filter japanese

# 美食鲜艳（高饱和暖调）
python xhs_clone.py "URL" --filter food_vivid

# 赛博朋克霓虹风
python xhs_clone.py "URL" --filter cyberpunk

# 经典黑白
python xhs_clone.py "URL" --filter bw_classic

# 港风（王家卫式暖调 + 颗粒）
python xhs_clone.py "URL" --filter hongkong

# 滤镜 + 配音 + BGM 组合
python xhs_clone.py "URL" --filter teal_orange --voice auto --bgm 氛围

# 对已有去重视频应用滤镜（不重新下载）
python xhs_clone.py --enhance-only "output/dedup/xxx/video_dedup.mp4" --filter kodak_portra
```

**预设架构（可自行扩展）**：

每个预设定义在 [core/filters.py](file:///d:/xhs/core/filters.py) 的 `FILTER_PRESETS` 字典中，结构如下：

```python
"teal_orange": {
    "color": {                          # 原生调色（FFmpeg eq/colorbalance/vibrance）
        "warm": 0.08, "saturation": 1.15, "contrast": 1.18,
        "brightness": -0.02, "vibrance": 0.2,
        "colorbalance": {"rs": 0.08, "bs": -0.1, "rm": 0.05, "bm": -0.08}
    },
    "lut": {"file": "teal_orange.cube", "strength": 0.7},  # LUT + 强度混合
    "effects": ["vignette", "grain_light"],                 # 特效链
    "meta": {"name_cn": "青橙色调", "category": "电影感", "desc": "经典电影青橙调色"},
}
```

- `color`：原生调色参数，支持 `warm`(暖色) `saturation`(饱和度) `contrast`(对比度) `brightness`(亮度) `gamma`(伽马) `vibrance`(自然饱和度) `denoise`(磨皮) `colorbalance`(色彩平衡 rs/gs/bs/rm/gm/bm)
- `lut`：3D LUT 文件 + strength(0-1，控制混合强度，<1.0 时用 split+blend 混合原画面)
- `effects`：特效列表，支持 `vignette`/`vignette_light`/`vignette_strong`(暗角三档) `grain`/`grain_light`(颗粒两档) `soft_glow`(柔焦光晕) `light_leak`(漏光) `glitch`(故障)
- `meta`：元信息（中文名/分类/描述）

**素材目录**：

| 目录 | 内容 | 生成方式 |
|------|------|---------|
| `assets/luts/` | 15 个 `.cube` LUT 文件（teal_orange/cinematic/kodak_portra 等） | `python assets/gen_luts.py` |
| `assets/overlays/light_leaks/` | 漏光素材（暖/冷/粉/日落） | `python assets/gen_overlays.py` |
| `assets/overlays/particles/` | 粒子素材（bokeh/dust/stars） | 同上 |
| `assets/overlays/frames/` | 边框素材（黑/白/胶片/宝丽来） | 同上 |
| `assets/overlays/vignettes/` | 暗角蒙版（轻/中/强/极强） | 同上 |
| `assets/overlays/sweeps/` | 扫光素材（暖/冷/白） | 同上 |

> LUT 文件缺失时预设仍可用，只是会跳过 LUT 调色（只应用原生调色 + 特效）。可用 `--list-filters` 查看 LUT 就绪状态。

### 3. 风格预设（按内容类型切换）

```bash
python xhs_clone.py "URL" --style-profile food       # 美食：更暖更鲜
python xhs_clone.py "URL" --style-profile beauty     # 美妆：强磨皮偏粉嫩
python xhs_clone.py "URL" --style-profile scenery    # 风景：高饱和低磨皮
python xhs_clone.py "URL" --style-profile lifestyle  # 生活家居（默认）
python xhs_clone.py "URL" --style-profile minimal    # 极简冷淡风
```

### 4. 对已有去重视频迭代调参（不重新下载）

```bash
python xhs_clone.py --enhance-only "output/dedup/xxx/video_dedup.mp4" --style-profile food --watermark "@号"
```
快速验证不同风格预设/水印效果，输出 `video_dedup_enhanced.mp4`。

### 5. 复刻 Live 图笔记

```bash
python xhs_clone.py https://www.xiaohongshu.com/explore/你的笔记ID
```

工具会自动识别 Live 图，对每张 Live 图同时处理静态图与动态视频，并为第一段动态视频生成剪映草稿。

### 6. 复刻图文笔记

```bash
python xhs_clone.py https://www.xiaohongshu.com/explore/你的笔记ID
```

每张图片都会被 Pillow 去重，输出为 `image_0.jpg`、`image_1.jpg` …

### 7. 只想拿到去重文件，不要剪映草稿

```bash
python xhs_clone.py <笔记URL> --no-jianying
```

### 8. 爆款封面 + 二创文案（默认开启）

处理完成后自动生成：
- **二创文案**：10 个爆款标题候选（按评分排序）+ 正文（含 hook 钩子）+ 推荐标签
- **4 种风格封面**：保留原封面 + 生成 4 种小红书爆款风格封面（3:4 竖屏 1080×1440）

```bash
# 默认全部开启
python xhs_clone.py <笔记URL>

# 只生成封面和文案，不做视频处理（搭配 --enhance-only）
python xhs_clone.py --enhance-only video.mp4 --no-jianying

# 自定义封面标题和风格
python xhs_clone.py <笔记URL> --cover-title "超好吃的番茄面" --cover-styles bigtext,bottombar

# 关闭封面/文案
python xhs_clone.py <笔记URL> --no-cover --no-copywrite
```

**4 种封面风格**：

| 风格 | 说明 | 适用 |
|------|------|------|
| `bigtext` | 大字报风：高饱和渐变背景 + 超大字标题 + emoji 贴纸 | 教程/种草，最吸睛 |
| `bottombar` | 底部字幕条风：原画面 + 底部渐变黑底 + 白字标题 | 故事/旅行，保留内容信息 |
| `topbar` | 顶部标题条风：顶部高亮色条 + 标题 | 干货/教程类爆款 |
| `magazine` | 杂志拼图风：三图拼接 + 中间大标题 | 对比/步骤类 |

### 8a. AI 生图封面（调用文生图模型，可选）

在常规 4 种 PIL 合成封面之外，还可以调用 AI 文生图模型生成更具视觉冲击力的封面。**默认关闭**，用 `--ai-cover` 开启。

**工作流程**：
1. LLM 根据笔记内容（标题/正文/标签）生成英文生图/图生图 prompt
2. 调用文生图/图生图模型（豆包 Seedream / OpenAI DALL-E 3 / GrsAI 聚合）生成封面底图
3. LLM 生成钩子文案（6-12 字，含钩子词）
4. PIL 叠加标题 + 钩子文案 + 装饰元素，输出最终封面

> 💡 **三层 LLM 服务商完全分离**（用 `--prompt-llm-provider` + `--image-llm-provider` 控制）：
> - **主 LLM**（`--llm-provider`）：负责二创文案、标题、口播改写、字幕纠错
> - **生图 prompt LLM**（`--prompt-llm-provider`）：负责生成英文生图 prompt 和封面钩子文案（推荐用便宜的对话模型如 deepseek）
> - **文生图/图生图 LLM**（`--image-llm-provider`）：负责实际出图（必须支持文生图，如 grsai）
>
> ```bash
> # 三层完全分离：二创用 deepseek，生图 prompt 用 qwen，文生图用 grsai
> python xhs_clone.py "URL" --llm-provider deepseek --ai-cover \
>     --prompt-llm-provider qwen --image-llm-provider grsai
> ```

**支持的生图模型**：

| 服务商 | 模型 | 价格 | 配置方式 |
|--------|------|------|---------|
| 豆包（火山引擎） | `doubao-seedream-3-0-t2i` | 按量计费 | `setx ARK_API_KEY "your-key"` + `--llm-provider doubao` |
| OpenAI | `dall-e-3` | $0.04/张起 | `setx OPENAI_API_KEY "sk-xxx"` + `--llm-provider openai` |
| **GrsAI（国内直连）** | `sora-image`(GPT4o) / `gpt-image-1.5` / `nano-banana` / `nano-banana-pro` | **0.02-0.09元/张** | `setx GRSAI_API_KEY "your-key"` + `--llm-provider grsai` |
| 通义千问 | 暂不支持 | - | 百炼兼容模式暂不支持 wanx 文生图 |
| DeepSeek | 暂不支持 | - | DeepSeek 暂无文生图能力 |

> ⚠️ 豆包用户需在火山引擎控制台开通 Seedream 文生图模型，并确保 API Key 有调用权限。
>
> 💡 **GrsAI 是国内直连的 AI 大模型 API 聚合平台**，价格便宜（0.02元/张起），支持 GPT Image 1.5、Nano Banana、Sora-image 等多种模型，国内访问无需魔法。API Key 获取：访问 https://grsai.com/zh/dashboard/api-keys 创建。

**6 种 AI 封面风格**（`--list-ai-covers` 查看全部）：

| 风格 | 名称 | 特点 | 钩子示例 |
|------|------|------|---------|
| `product_hero` | 产品主角 | 产品居中特写 + 高级感背景 + 底部爽点文案 | 3秒提亮 真的绝了 |
| `before_after` | 前后对比 | 左右分屏效果对比 + BEFORE/AFTER 标签 | 7天实测 真实对比 |
| `hook_closeup` | 钩子特写 | 夸张视觉特写 + 大字钩子文案 | 99%的人不知道 |
| `scene_lifestyle` | 场景生活 | 产品使用场景 + 氛围感 + 品牌标签 | 日常好物分享 |
| `list_infographic` | 清单信息图 | 留白背景 + 数字步骤 + 简洁信息 | 3步搞定 简单又快 |
| `emotion_card` | 情绪卡片 | 纯色渐变背景 + 超大字情绪文案 | 真的会很治愈 |

```bash
# 查看全部 AI 封面风格和支持的生图模型
python xhs_clone.py --list-ai-covers

# 用豆包 Seedream 生成全部 6 种 AI 封面
python xhs_clone.py "URL" --llm-provider doubao --ai-cover

# 只生成产品主角 + 前后对比两种
python xhs_clone.py "URL" --llm-provider doubao --ai-cover --ai-cover-styles product_hero,before_after

# 用 DALL-E 3 生成钩子特写 + 情绪卡片
python xhs_clone.py "URL" --llm --llm-provider openai --ai-cover --ai-cover-styles hook_closeup,emotion_card

# 用 GrsAI 生成全部 6 种 AI 封面（国内直连，最便宜，0.02元/张）
python xhs_clone.py "URL" --llm-provider grsai --ai-cover

# 用 GrsAI 指定 nano-banana-pro 模型（质量更高，0.09元/张）
python xhs_clone.py "URL" --llm-provider grsai --grsai-image-model nano-banana-pro --ai-cover

# 💰 成本优化：文生图用 GrsAI sora-image，生图 prompt 用 DeepSeek（更便宜的对话模型）
python xhs_clone.py "URL" --llm-provider grsai --ai-cover --prompt-llm-provider deepseek

# 指定 prompt 模型（如用 deepseek-chat 生成 prompt，文生图仍用 grsai）
python xhs_clone.py "URL" --llm-provider grsai --ai-cover --prompt-llm-provider deepseek --prompt-llm-model deepseek-chat

# 文生图用豆包 Seedream，生图 prompt 用通义千问（qwen-plus）
python xhs_clone.py "URL" --llm-provider doubao --ai-cover --prompt-llm-provider qwen --prompt-llm-model qwen-plus

# 🎨 三层完全分离：二创文案用 deepseek，生图 prompt 用 qwen，文生图用 grsai
python xhs_clone.py "URL" --llm-provider deepseek --ai-cover \
    --prompt-llm-provider qwen --image-llm-provider grsai

# 🖼️ 图生图模式：基于原封面重新生成，从不同视角/光线/风格重构（推荐 nano-banana-pro）
python xhs_clone.py "URL" --llm-provider deepseek --ai-cover \
    --image-llm-provider grsai --image-llm-model nano-banana-pro --image-mode i2i

# 图生图 + 三层分离：二创用 deepseek，prompt 用 qwen，图生图用 grsai nano-banana-pro
python xhs_clone.py "URL" --llm-provider deepseek --ai-cover \
    --prompt-llm-provider qwen \
    --image-llm-provider grsai --image-llm-model nano-banana-pro --image-mode i2i

# 指定生图尺寸（豆包支持 960x1280 接近 3:4）
python xhs_clone.py "URL" --llm-provider doubao --ai-cover --ai-cover-size 960x1280

# 同时生成常规封面 + AI 生图封面
python xhs_clone.py "URL" --llm-provider doubao --ai-cover --cover-styles bigtext,bottombar
```

> 💡 **说明**：
> - AI 生图封面与常规 4 种 PIL 封面**并存**，不会互相覆盖（文件名 `cover_ai_<风格>.jpg` vs `cover_<风格>.jpg`）
> - LLM 不可用时，生图 prompt 和钩子文案会自动回退到模板（仍能生成，但效果不如 LLM 智能化）
> - 单张生图约需 5-30 秒（GrsAI 流式响应可能稍慢），6 种风格全量生成约 1-5 分钟
> - 生图尺寸建议：豆包 `960x1280` 或 `1024x1024`，DALL-E 3 `1024x1024`（DALL-E 3 不支持 3:4 竖屏，会自动裁剪）
> - GrsAI 仅支持 3 种比例：`1:1` / `3:2` / `2:3`，工具会自动把 `1024x1024` 等尺寸转换成最接近的比例
> - **三层 LLM 服务商可完全分离**：
>   - `--llm-provider` = 主 LLM（二创文案、标题、口播改写、字幕纠错）
>   - `--prompt-llm-provider` = 生图 prompt LLM（生成英文生图 prompt 和钩子文案，推荐用便宜的对话模型）
>   - `--image-llm-provider` = 文生图/图生图 LLM（实际出图，必须支持文生图）
>   - 不指定后两者时，与 `--llm-provider` 相同（向后兼容）
> - **图生图模式**（`--image-mode i2i`）：基于原封面 URL 重新生成，LLM 会生成含视角/光线/构图重构指令的 prompt，从全新角度呈现原图主体。需笔记有原封面 URL（小红书笔记自动满足），文生图模型推荐 `nano-banana-pro`（图生图效果最好）。无原封面时自动回退到文生图模式

**二创文案评分维度**（本地模型，无 LLM 依赖）：
- 长度适中（15-25 字最佳）
- 含数字（增加可信度）
- 含疑问/感叹（引发情绪）
- 含爆款关键词（绝了/天花板/保姆级等）
- 含钩子词（别再/收藏/最后/真相等）
- 含痛点/悬念（错了/误区/踩坑等）

**底图来源**（优先级）：原封面 URL → 视频抽帧（30% 位置跳过片头）→ 纯色背景兜底

### 8b. LLM 大模型二创（可选，效果更智能）

本地模板库覆盖 7 大内容类型、100+ 标题模板、20 个 hook 句式。如果需要更智能、更贴切的文案，可以接入大模型（豆包/通义/OpenAI 三选一）。

**特点**：
- LLM 生成的文案更懂小红书调性，有真实的情绪和悬念
- 封面标题会结合 whisper 识别的视频字幕内容，更贴合视频
- **优雅降级**：API 不可用时自动回退到本地模板库，不影响使用

#### 配置（四选一）

**豆包（推荐，中文小红书调性最佳）**：
```bash
# 火山引擎控制台 → 模型推理 → 创建 API Key
setx LLM_PROVIDER "doubao"
setx ARK_API_KEY "your-ark-api-key"
# 可选：setx LLM_MODEL "doubao-pro-32k"
pip install openai
```

**通义千问（价格更低）**：
```bash
# 阿里云百炼平台 → 创建 API Key
setx LLM_PROVIDER "qwen"
setx DASHSCOPE_API_KEY "your-dashscope-api-key"
# 可选：setx LLM_MODEL "qwen-plus"
pip install openai
```

**DeepSeek（推理能力强）**：
```bash
# DeepSeek 开放平台 → 创建 API Key
setx LLM_PROVIDER "deepseek"
setx DEEPSEEK_API_KEY "your-deepseek-api-key"
# 可选：setx LLM_MODEL "deepseek-chat"（或 deepseek-reasoner 用推理模型）
pip install openai
```

**OpenAI（需代理）**：
```bash
setx LLM_PROVIDER "openai"
setx OPENAI_API_KEY "sk-xxx"
# 可选：setx LLM_MODEL "gpt-4o-mini"
pip install openai
```

#### 使用

```bash
# 启用 LLM（失败自动回退模板库）
python xhs_clone.py "URL" --llm

# 强制用 LLM，失败不回退（调试用）
python xhs_clone.py "URL" --llm --llm-only

# 指定服务商和模型（覆盖环境变量）
python xhs_clone.py "URL" --llm --llm-provider qwen --llm-model qwen-plus

# 用 CLI 直接传 API Key（不推荐，建议用环境变量）
python xhs_clone.py "URL" --llm --llm-api-key "your-key"
```

#### LLM 生效范围

| 功能 | LLM 作用 | 回退方案 |
|------|---------|---------|
| 二创标题候选 | LLM 生成 5-10 个标题 | 本地模板库生成 |
| 二创正文 | LLM 改写正文（含 hook） | 模板拼接 |
| 封面标题 | LLM 生成 6-12 字短标题（结合字幕内容） | 用二创 Top1 标题 |

#### 成本估算

- 一次改写约 500-1000 token
- 豆包：约 ¥0.001/次（输入 ¥0.8/百万 token）
- 通义：约 ¥0.0005/次
- DeepSeek：约 ¥0.001/次（输入 ¥1/百万 token）
- OpenAI gpt-4o-mini：约 $0.0002/次

### 1b-8. 独立特效（44 个，可叠加，类似剪映特效）

滤镜预设（`--filter`）是调色+LUT+特效的套餐，一次只能选一个。**独立特效**（`--effect`）是单独的视觉特效，可以多次指定叠加多个，与滤镜解耦，灵活组合。

**44 个特效分 5 大类**（`--list-effects` 查看全部）：

| 分类 | 特效 | 说明 |
|------|------|------|
| **画面（10）** | `blur` 模糊 / `sharpen` 锐化 / `pixelate` 像素化 / `mosaic` 马赛克 / `rgb_split` RGB分离 / `glitch` 故障 / `shake` 抖动 / `zoom_pulse` 缩放呼吸 / `mirror` 镜像 / `wave` 水波纹 | 纯 FFmpeg 滤镜，速度快 |
| **光影（10）** | `light_leak_warm` 暖色漏光 / `light_leak_cool` 冷色漏光 / `light_leak_sunset` 日落漏光 / `light_leak_pink` 粉色漏光 / `sweep_warm` 暖色扫光 / `sweep_cool` 冷色扫光 / `sweep_white` 白色扫光 / `lens_flare` 镜头光晕 / `god_rays` 丁达尔光柱 / `bloom` 辉光 | PNG 素材 + geq 生成 |
| **氛围粒子（10）** | `snow` 雪花 / `rain` 雨滴 / `petals` 樱花飘落 / `stars_white` 白色星空 / `stars_gold` 金色星空 / `bokeh_warm` 暖色光斑 / `bokeh_cool` 冷色光斑 / `dust` 灰尘 / `bubbles` 气泡 / `fireworks` 烟花 | geq 动态生成 + PNG 动画 |
| **边框（9）** | `frame_film` 胶片框 / `frame_polaroid` 拍立得框 / `frame_white_thick` 白色粗框 / `frame_white_thin` 白色细框 / `frame_black_thin` 黑色细框 / `vignette_light` 轻暗角 / `vignette_medium` 中暗角 / `vignette_strong` 强暗角 / `letterbox` 电影遮幅 | PNG 叠加 + drawbox |
| **复古胶片（5）** | `vhs` VHS录像带 / `old_film` 老电影 / `film_scratch` 胶片划痕 / `flicker` 闪烁 / `halation` 高光泛光 | FFmpeg 原生组合 |

**特效与滤镜的关系**：
- `--filter` 选一个滤镜套餐（调色+LUT+内置特效）
- `--effect` 叠加独立特效（可多个），在滤镜之后应用
- 两者可同时使用：`--filter teal_orange --effect snow --effect frame_film`

**特效实现方式**：
- **FFmpeg 原生**：直接用 FFmpeg 滤镜（blur/noise/eq 等），速度最快
- **PNG overlay**：叠加透明 PNG 素材（漏光/边框/暗角/光斑），用 blend 混合
- **PNG 动画**：PNG + overlay 位置动画（扫光/摇摆/飘落/闪烁）
- **geq 生成**：用 FFmpeg geq 滤镜数学生成粒子（雪花/雨/樱花/光晕/光柱），无需素材但计算量大

> ⚠️ **geq 粒子特效（snow/rain/petals/bubbles/fireworks/lens_flare/god_rays）计算量大**，在 1080x1920 全分辨率上处理长视频会很慢（约为正常速度的 5-10 倍）。建议：
> - 短视频（<15s）可直接用
> - 长视频建议先用 `--effect light_leak_warm`（PNG）等快速特效
> - 或降低分辨率测试效果后再用全分辨率渲染

```bash
# 查看全部 44 个特效
python xhs_clone.py --list-effects

# 雪花 + 暖色漏光（冬日氛围）
python xhs_clone.py "URL" --effect snow --effect light_leak_warm

# 胶片框 + 强暗角 + 老电影（复古胶片风）
python xhs_clone.py "URL" --effect frame_film --effect vignette_strong --effect old_film

# 樱花飘落 + 粉色漏光（少女风）
python xhs_clone.py "URL" --effect petals --effect light_leak_pink

# 滤镜 + 特效组合（青橙调色 + 白色扫光 + 电影遮幅）
python xhs_clone.py "URL" --filter teal_orange --effect sweep_white --effect letterbox

# 调整特效强度（1.5 倍，更明显）
python xhs_clone.py "URL" --effect snow --effect-intensity 1.5

# 对本地视频应用特效
python xhs_clone.py "你的视频.mp4" --effect frame_polaroid --effect light_leak_sunset

# enhance-only 模式（对已去重视频直接加特效）
python xhs_clone.py --enhance-only "output/dedup/xxx/video_dedup.mp4" --effect old_film --effect flicker
```

**特效叠加顺序**：按 `--effect` 指定顺序依次应用（前面的特效输出作为后面的输入），叠加越多处理越慢。

**特效强度**（`--effect-intensity`）：
- 0.0-2.0，默认 1.0
- 影响 PNG overlay 的透明度和 blend 混合强度
- 对 FFmpeg 原生特效（blur/pixelate 等）无影响（这些是确定性滤镜）

---

## 三、命令行参数详解

```
python xhs_clone.py <笔记URL或ID> [选项]
```

| 参数 | 说明 | 默认 |
|------|------|------|
| `note` | 笔记 URL 或纯 note_id（位置参数，URL 需用 `"` 包住） | 必填 |
| `--no-jianying` | 不生成剪映草稿，仅做 ffmpeg/Pillow 去重 | 关闭（即生成草稿） |
| `--speed FLOAT` | 视频变速系数（如 1.06 = 加快 6%） | 1.06 |
| `--mirror` | 开启水平镜像去重（**会颠倒文字**，无字幕/招牌的笔记才用） | 关闭 |
| `--quality INT` | 图片 JPEG 质量（1-100） | 95 |
| `--crop-ratio FLOAT` | 四边裁剪比例（0-0.2，同时作用于视频和图片，裁后放大回原尺寸） | 0.06 |
| `--img-brightness F` | 图片亮度因子（1.0=不变，>1 提亮） | 1.05 |
| `--img-contrast F` | 图片对比度因子（1.0=不变） | 1.06 |
| `--img-saturation F` | 图片饱和度因子（1.0=不变，>1 更鲜艳） | 1.12 |
| `--img-sharpness F` | 图片锐度因子（1.0=不变，>1 更锐利） | 1.18 |
| `--noise-amount INT` | 图片噪点强度（0-50，0=关闭） | 5 |
| `--no-strip-exif` | 保留图片 EXIF 元数据（默认去除） | 去除 |
| `--img-format` | 图片输出格式: JPEG / PNG / WEBP | JPEG |
| `--preset` | x264 编码预设：ultrafast/superfast/veryfast/faster/**fast**/medium/slow/slower/veryslow | fast |
| `--crf INT` | x264 画质（0-51，越小越清晰文件越大；18=高画质，23=中等，28=可接受小文件） | 18 |
| `--no-enhance` | 关闭小红书风格增强（只去重） | 关闭（即做增强） |
| `--style-profile` | 风格预设：default/lifestyle/food/beauty/scenery/minimal | default |
| `--filter STR` | 滤镜预设名（55 个可选，`--list-filters` 查看全部）。如 `teal_orange`/`kodak_portra`/`japanese`/`food_vivid`/`cyberpunk` | 无 |
| `--list-filters` | 列出全部滤镜预设（按 7 大分类组织） | — |
| `--effect STR` | 独立特效名（44 个可选，`--list-effects` 查看全部）。可多次指定叠加多个。如 `snow`/`light_leak_warm`/`frame_film`/`old_film` | 无 |
| `--list-effects` | 列出全部特效（按 5 大分类：画面/光影/氛围粒子/边框/复古胶片） | — |
| `--effect-intensity FLOAT` | 特效强度倍数 0.0-2.0（影响透明度/混合强度） | 1.0 |
| `--watermark STR` | 右下角水印文字（如 `--watermark "@我的号"`） | 无 |
| `--no-stickers` | 关闭自动贴纸 overlay | 开启 |
| `--sticker-count N` | 贴纸数量（1-4） | 2 |
| `--sticker-opacity F` | 贴纸透明度 0-1（如 0.05 = 5%） | 0.02 |
| `--no-sweep` | 关闭扫光效果 | 开启 |
| `--sweep-opacity F` | 扫光透明度 0-1 | 0.05 |
| `--no-caption` | 关闭底部字幕条 | 开启 |
| `--caption STR` | 自定义字幕条文字（默认用笔记标题） | 笔记标题 |
| `--caption-style` | 字幕条样式: bottom / top / none | bottom |
| `--no-noise` | 关闭噪点纹理 | 开启 |
| `--pip PATH` | 画中画素材视频路径 | 无 |
| `--enhance-only PATH` | 跳过下载/去重，直接对已有视频做风格增强+二创（用于调参） | — |
| `--no-erase-subtitle` | 不抹除原视频字幕（默认抹除） | 抹除 |
| `--erase-mode` | 字幕抹除方式: blur / delogo / cover / **inpaint**(推荐,无模糊) / **ocr-inpaint**(最自然) | blur |
| `--inpaint-radius INT` | inpaint/ocr-inpaint 修复半径（默认 4） | 4 |
| `--inpaint-dilate INT` | inpaint/ocr-inpaint mask 膨胀半径（默认 2） | 2 |
| `--ocr-inpaint-samples INT` | ocr-inpaint 模式 OCR 采样帧数（默认 12） | 12 |
| `--no-auto-detect-subtitle` | 关闭字幕区域自动识别（用默认区域） | 自动识别 |
| `--detect-subtitle-only PATH` | 只检测字幕区域并打印，不做其他处理（用于确认） | — |
| `--detect-frames N` | 自动识别抽帧数量（3-8，越多越准但越慢） | 5 |
| `--subtitle-style` | 新字幕样式: default/bold_yellow/clean_box/cute_pink/top_title | default |
| `--subtitle-position` | 字幕位置: auto(跟随原字幕) / bottom / top / center | auto |
| `--subtitle-source` | 字幕文本来源: auto(智能) / whisper(原识别) / llm-fix(LLM纠错,推荐) / note(笔记文案) | auto |
| `--no-subtitle` | 不渲染新字幕（只抹除原字幕） | 渲染 |
| `--whisper` | 用 whisper 语音识别提取字幕（需 `pip install -U openai-whisper`） | 关闭 |
| `--whisper-model` | whisper 模型: tiny/base/small/medium | base |
| `--voice` | AI 配音别名（如 `xiaoxiao`/`yunjian`/`auto`，`--list-voices` 查看全部 26 个）。`auto` = 根据笔记标签/标题自动匹配。指定后自动启用配音 | 关闭 |
| `--voice-text` | 配音文本（默认用笔记标题+描述） | 笔记文案 |
| `--voice-rate` | 配音语速（如 `+10%`加快 / `-10%`减慢） | `+0%` |
| `--voice-pitch` | 配音音调（如 `+5Hz`偏高 / `-5Hz`偏低） | `+0Hz` |
| `--voice-volume` | 配音音量（如 `+20%`变大 / `-20%`变小） | `+0%` |
| `--keep-bg-audio` | 保留原视频音频作为BGM（降音量混合），默认完全替换 | 替换 |
| `--bg-volume F` | 保留BGM时的音量 0-1 | 0.15 |
| `--no-voice` | 关闭 AI 配音 | — |
| `--no-fit-duration` | 不调整配音时长以匹配视频 | 自动匹配 |
| `--no-clean-text` | 不清理配音文本（保留#标签/emoji） | 清理 |
| `--voice-rewrite` | 用 LLM 对 whisper 识别的原口播做二创改写后再配音（需配合 `--whisper` `--voice`） | 关闭 |
| `--rewrite-style` | 二创风格: casual/professional/passion/gentle/knowledge/humor/concise | casual |
| `--list-rewrite-styles` | 列出全部 7 种口播二创风格 | — |
| `--list-voices` | 列出所有 26 个 AI 配音 | — |
| `--bgm` | 背景音乐：风格名(治愈/欢快/古风) / 曲名 / auto(按笔记标签自动选)。指定后自动启用BGM，与配音共存 | 关闭 |
| `--bgm-volume` | BGM 音量 0-1（默认 0.15 = 15% 背景音，配音为主） | 0.15 |
| `--no-bgm` | 关闭 BGM | — |
| `--no-bgm-fade` | 不给 BGM 加淡入淡出 | 淡入淡出 |
| `--list-bgm` | 列出可用 BGM + 100+ 首小红书热门 BGM 推荐清单 | — |
| `--download-bgm` | 从 `assets/bgm/_sources.json` 批量下载免版税音乐并自动重命名归类 | — |
| `--dry-run` | 与 `--download-bgm` 搭配，仅预览不实际下载 | — |
| `--list-sources` | 列出合法免版税音乐来源（Pixabay/Bensound/FMA/Musopen 等） | — |
| `--remove-wm` | **启用动态水印去除**（颜色检测模式，无需额外依赖） | 关闭 |
| `--wm-ocr` | **启用 OCR 模式**（推荐）：PaddleOCR 识别水印文字，能区分水印和字幕 | 关闭 |
| `--wm-text STR` | OCR 模式：水印文字关键词（逗号分隔，如 `@原创,搬运`）。不指定则自动识别 @ + 常见水印词 | 自动 |
| `--wm-ocr-samples INT` | OCR 模式：采样帧数（越多越准但越慢） | 10 |
| `--wm-match-threshold FLOAT` | OCR 模式：模板匹配置信度阈值 0-1（越低越宽松） | 0.5 |
| `--wm-replace STR` | OCR 模式：替换文字（如 `@你的号`），去除原水印后在原位置覆盖自己的文字，**跟随原水印移动轨迹**。自动启用 OCR 模式 | 无 |
| `--wm-region STR` | 颜色模式：水印起始区域 `x,y,w,h`（如 `5%,5%,30%,10%`） | 自动检测 |
| `--wm-threshold INT` | 颜色模式：亮度阈值 0-255（越小越严格） | 200 |
| `--wm-min-area INT` | 颜色模式：水印最小像素面积 | 200 |
| `--wm-dilate INT` | 膨胀半径（扩大修复区域覆盖描边） | 3 |
| `--wm-no-track` | 颜色模式：关闭追踪器，每帧独立检测 | 开启追踪 |
| `--no-cover` | 不生成封面（默认生成 4 种风格 + 保留原封面） | 生成 |
| `--no-copywrite` | 不生成二创文案（默认生成爆款标题候选 + 正文 + 标签） | 生成 |
| `--cover-styles STR` | 封面风格（逗号分隔）: `bigtext,bottombar,topbar,magazine` | 全部 |
| `--cover-title STR` | 封面标题文字（默认用二创 Top1 标题） | 自动 |
| `--ai-cover` | 启用 AI 生图封面（需配合 `--llm-provider doubao` / `openai` / `grsai`） | 关闭 |
| `--ai-cover-styles` | AI 封面风格(逗号分隔): product_hero/before_after/hook_closeup/scene_lifestyle/list_infographic/emotion_card | 全部 |
| `--ai-cover-size` | AI 生图尺寸: 1024x1024 / 960x1280(豆包) / 1792x1024(DALL-E 3) | 1024x1024 |
| `--grsai-image-model` | GrsAI 文生图模型（仅 `--llm-provider grsai` 时生效）: sora-image / gpt-image-1.5 / nano-banana / nano-banana-pro | sora-image |
| `--prompt-llm-provider` | 生图 prompt 专用 LLM 服务商（仅 `--ai-cover` 时生效）: doubao / qwen / deepseek / openai / grsai。可与其他 LLM 参数分离，如文生图用 grsai、生图 prompt 用 deepseek | 同 `--llm-provider` |
| `--prompt-llm-model` | 生图 prompt 专用 LLM 模型名（如 deepseek-chat / gpt-4o / qwen-plus） | 服务商默认 |
| `--image-llm-provider` | 文生图/图生图专用 LLM 服务商（仅 `--ai-cover` 时生效）: doubao / openai / grsai。用于把图片生成独立到支持文生图的服务商 | 同 `--llm-provider` |
| `--image-llm-model` | 文生图/图生图模型名（如 nano-banana-pro / sora-image / gpt-image-1.5 / dall-e-3）。图生图推荐 nano-banana-pro | 服务商默认 |
| `--image-mode` | AI 封面图片生成模式（仅 `--ai-cover` 时生效）: t2i=文生图（默认） / i2i=图生图（基于原封面重新生成，从不同视角/光线/风格重构） | t2i |
| `--list-ai-covers` | 列出全部 AI 生图封面风格 + 支持的生图模型 | — |
| `--n-titles INT` | 二创标题候选数量 | 10 |
| `--llm` | 启用 LLM 大模型生成文案和封面标题（失败回退模板库） | 关闭 |
| `--llm-only` | 强制用 LLM，失败不回退（调试用） | 关闭 |
| `--llm-provider` | LLM 服务商: `doubao`/`qwen`/`deepseek`/`openai`（也可用环境变量 `LLM_PROVIDER`） | doubao |
| `--llm-model STR` | LLM 模型名（不指定用服务商默认） | 服务商默认 |
| `--llm-api-key STR` | LLM API Key（建议用环境变量） | 环境变量 |
| `--list-drafts` | 列出当前剪映草稿目录下的草稿 | — |
| `-v, --verbose` | 详细调试日志 | 关闭 |

### URL 支持的格式

以下格式都能自动识别：

- `https://www.xiaohongshu.com/explore/<ID>`
- `https://www.xiaohongshu.com/discovery/item/<ID>`
- `https://www.xiaohongshu.com/note/<ID>?xsec_token=...`
- 分享文本里粘贴的链接
- 纯 24 位 note_id

---

## 四、去重策略说明

### 视频去重（ffmpeg）

| 手段 | 参数 | 作用 |
|------|------|------|
| 水平镜像 | `mirror=True` | 翻转画面，绕过画面指纹 |
| 变速 | `speed=1.05` | 时长与帧间隔变化，绕过时长/帧哈希 |
| 四边裁剪+放大 | `crop_ratio=0.04` | 改变画面像素布局 |
| 色彩调整 | `brightness/contrast/saturation/gamma` | 改变直方图分布 |
| 音频变调 | `asetrate + aresample` | 绕过音轨指纹 |
| 音频变速 | `atempo=1.05` | 与视频同步 |
| 轻微噪点 | `noise=alls=6` | 破坏逐像素匹配 |
| 轻微锐化 | `unsharp` | 改变高频分量 |
| 去元数据 | `map_metadata=-1` | 清除制作信息指纹 |
| 强制重编码 | `libx264 + aac, CRF=18` | 改变编码结构与码流 |

### 视频编码参数（`--preset` / `--crf`）

去重和风格增强都用 x264 编码，可通过两个参数自定义速度/质量/文件大小的权衡：

**`--preset`（编码速度预设）**

CRF 固定时，preset 只影响**文件大小**和**编码耗时**，**画质几乎相同**。

| preset | 速度 | 文件大小 | 适用场景 |
|--------|------|---------|---------|
| `ultrafast` | 最快 | 最大 | 长视频超时救急、快速预览 |
| `superfast` | 很快 | 大 | 测试 |
| `veryfast` | 快 | 中大 | 批量处理 |
| `faster` | 较快 | 中 | 批量处理 |
| **`fast`** ← 默认 | **快（约 2x medium）** | **中** | **去重/二创（推荐）** |
| `medium` | 中（1x） | 较小 | x264 官方默认 |
| `slow` | 慢 | 小 | 追求小文件 |
| `slower` | 很慢 | 更小 | 高质量存档 |
| `veryslow` | 最慢 | 最小 | 冷压存档 |

**`--crf`（画质/文件大小权衡）**

| CRF | 画质 | 文件大小 | 适用场景 |
|-----|------|---------|---------|
| 16 | 极高 | 很大 | 高质量存档 |
| **18** ← 默认 | **高** | **中** | **去重/二创（推荐）** |
| 23 | 中等 | 小 | 中等质量 |
| 28 | 可接受 | 很小 | 草稿/预览 |

**实用组合**：

```bash
# 默认（推荐，速度与质量平衡）
python xhs_clone.py "URL"

# 长视频超时救急（速度优先，文件会大）
python xhs_clone.py "URL" --preset ultrafast --crf 23

# 追求小文件（慢但省空间）
python xhs_clone.py "URL" --preset slow --crf 23

# 最高画质（慢但极清晰）
python xhs_clone.py "URL" --preset slow --crf 16
```

> 也可在 [config.py:60](file:///d:/xhs/config.py#L60) 修改 `VIDEO_DEDUP["preset"]` 和 `["crf"]` 改默认值。

### 图片去重（Pillow）

| 手段 | 配置参数 | CLI 参数 |
|------|------|------|
| 水平镜像 | `mirror=True` | `--mirror` |
| 四边裁剪+放大回原尺寸 | `crop_ratio=0.04, resize_back=True` | `--crop-ratio 0.04` |
| 亮度微调 | `brightness=1.05` | `--img-brightness 1.05` |
| 对比度微调 | `contrast=1.06` | `--img-contrast 1.06` |
| 饱和度微调 | `saturation=1.12` | `--img-saturation 1.12` |
| 锐度微调 | `sharpness=1.18` | `--img-sharpness 1.18` |
| 轻微高斯噪点 | `noise_amount=4` | `--noise-amount 4` |
| 去除 EXIF | `strip_exif=True` | `--no-strip-exif` 可关闭 |
| 强制重编码 | JPEG progressive, 4:2:2 subsampling, quality=95 | `--quality 95` / `--img-format PNG` |

**手动上传图片**也完全支持上述所有手段——直接传本地图片路径即可：

```bash
# 本地图片去重（自动识别 .jpg/.jpeg/.png/.webp/.bmp）
python xhs_clone.py "D:\photos\my_image.jpg"

# 自定义去重参数
python xhs_clone.py "D:\photos\my_image.jpg" --crop-ratio 0.04 --noise-amount 4 --img-brightness 1.02 --no-jianying

# 输出为 PNG
python xhs_clone.py "D:\photos\my_image.jpg" --img-format PNG --no-jianying
```

### Live 图去重

同时对静态图（Pillow）与动态视频（ffmpeg）应用上述策略；静态图保证两张图片哈希不同，动态视频保证音视频指纹不同。

所有参数都集中在 `config.py`，可按需调整强度。

---

## 五、剪映草稿说明

工具会在剪映草稿根目录（自动探测）下创建草稿：

```
%LOCALAPPDATA%\JianyingPro\User Data\Projects\com.lveditor.draft\XHS视频_<ID>\
├── draft_content.json     # 时间线、片段、效果
├── draft_meta_info.json   # 草稿元信息
└── video_dedup.mp4        # 已去重的视频（已复制进草稿）
```

草稿里：
- 视频已作为片段放在时间线起点
- 片段 `clip.hflip=1`（水平镜像）
- 片段 `speeds=[1.05]`（变速 1.05）
- 已注册到 `root_meta_info.json`

**使用流程：**
1. 运行工具（默认即生成草稿）
2. 打开剪映专业版 → 「草稿」列表 → 选择 `XHS视频_<ID>`
3. 可直接导出，或继续添加字幕、滤镜、画中画等二创
4. 导出后的视频即「剪映渲染过」的去重视频

> 如剪映未识别到草稿：检查 `config.py` 里的 `JIANYING_DRAFT_ROOT` 是否指向你的剪映草稿目录；也可以手动把 `output/jianying_drafts/` 下的草稿文件夹整个复制到剪映草稿目录。

---

## 六、输出目录结构

```
output/
├── _downloads/<笔记ID>/          # 原始下载素材
│   ├── video_0.mp4 / image_0.jpg / live_0.mp4 ...
│   └── content.json              # 原始笔记数据
├── dedup/<笔记ID>/               # ✅ 去重后成品（你要用的就是这个）
│   ├── video_dedup.mp4           # 去重视频
│   ├── image_0.jpg ...           # 去重图片
│   ├── live_0.jpg + live_0.mp4   # 去重 Live 图
│   ├── content_clone.txt         # 复刻文案（原标题+正文+标签，可直接复制发布）
│   ├── content_clone.json
│   ├── copywriting_clone.txt     # 二创文案（10个爆款标题候选+正文+推荐标签）
│   ├── cover_原封面.jpg          # 原封面（保留）
│   ├── cover_原封面_去重.jpg      # 原封面去重版（镜像/裁剪/调色/噪点/去EXIF，破坏哈希查重）
│   ├── cover_bigtext.jpg         # 大字报风封面
│   ├── cover_bottombar.jpg       # 底部字幕条风封面
│   ├── cover_topbar.jpg          # 顶部标题条风封面
│   └── cover_magazine.jpg        # 杂志拼图风封面
└── jianying_drafts/<草稿名>/      # 剪映草稿副本（同时已安装到剪映目录）
    ├── draft_content.json
    ├── draft_meta_info.json
    └── video_dedup.mp4
```

---

## 七、常见问题（FAQ）

**Q1：提示「无法解析 __INITIAL_STATE__，可能需要登录或被反爬拦截」？**
A：小红书对未登录访问有反爬。解决办法：
1. 用浏览器打开笔记，确认能正常显示
2. 稍等几分钟重试（频控一般 10-30 分钟解除）
3. 若长期失败，可在浏览器登录后把 cookie 复制进 `config.py` 的 `HEADERS`（自行实现）

**Q2：剪映里看不到草稿？**
A：① 确认剪映已关闭再重新打开；② 检查 `config.py` 的 `JIANYING_DRAFT_ROOT`；③ 手动复制 `output/jianying_drafts/` 下对应文件夹到剪映草稿目录。

**Q3：视频去重后画面/音质变差？**
A：默认 CRF=18 视觉无损。如需更高画质，编辑 `config.py` 把 `crf` 调小（如 14）；如需更小体积，调大（如 23）。

**Q4：去重强度不够/太强？**
A：所有强度参数在 `config.py` 的 `VIDEO_DEDUP` / `IMAGE_DEDUP` / `LIVE_DEDUP` 中调整。例如关闭镜像设 `mirror=False`，加大变速设 `speed=1.1`。

**Q5：下载速度慢/失败？**
A：网络问题，工具已内置 3 次重试。可设置代理环境变量：`set HTTPS_PROXY=http://127.0.0.1:7890`。

**Q6：能批量处理吗？**
A：当前为单条处理。可写个批处理脚本循环调用：
```bat
for %%u in (url1 url2 url3) do python xhs_clone.py %%u
```

**Q7：AI 配音不自然/读错字？**
A：
1. 配音文本会自动清理 `#标签`/emoji/URL，如不想清理用 `--no-clean-text`
2. 用 `--voice-text "自定义文本"` 指定更口语化的文案
3. 调语速：`--voice-rate -8%`（减慢更清晰）
4. 调音调：`--voice-pitch +3Hz`（偏高更年轻）
5. 不同配音音色差异大，用 `--list-voices` 按场景选择

**Q8：配音比视频长/短不同步？或者配音只有几秒人声后面全是静音？**
A：默认会自动用 atempo 变速匹配视频时长（不变调）。如关闭了 `--no-fit-duration` 又出现不同步，去掉该参数即可。

配音只有几秒人声的原因：配音文本太短（如笔记正文主要是 #标签，清理后只剩标题）。解决办法：
1. **推荐：配合 `--whisper` 使用** — whisper 识别视频原语音作为配音文本，时长接近视频，不会出现大段静音：
   ```bash
   python xhs_clone.py "URL" --whisper --whisper-model small --voice auto
   ```
2. 用 `--voice-text "完整解说词..."` 指定更长的配音文本
3. 用 `--voice-rate` 手动调整语速

> 当配音远短于视频（如 4s 配音 vs 76s 视频，比例 < 0.3）时，工具会自动改用"原速播放 + 尾部静音填充"策略，避免极端慢放让人声变成低频杂音。

**Q9：edge-tts 报错/配音生成失败？**
A：edge-tts 依赖网络访问微软语音服务。检查网络连接，或设置代理：`set HTTPS_PROXY=http://127.0.0.1:7890`。重试通常能解决临时网络问题。

**Q10：BGM 怎么用？为什么提示"无可用音乐文件"？**
A：工具不内置音乐文件（版权原因），需自行下载：
1. 运行 `python xhs_clone.py --list-bgm` 查看 17 个风格共 100+ 首推荐曲目
2. 按曲名自行搜索下载（网易云/QQ音乐/抖音等）
3. 保存到 `assets/bgm/`，文件名规范：`风格_曲名.mp3`（如 `治愈_起风了.mp3`）
4. 然后用 `--bgm auto`（自动选风格）/ `--bgm 治愈`（按风格）/ `--bgm 起风了`（按曲名）

**Q11：配音和 BGM 同时开启会冲突吗？**
A：不会。配音为主音轨，BGM 为背景音（默认 15% 音量），两者用 ffmpeg `amix` 混合。推荐组合：`--voice auto --bgm auto`。用 `--bgm-volume` 调节 BGM 音量平衡。

**Q12：BGM 比视频短会循环吗？**
A：会。BGM 用 `stream_loop=-1` 无限循环到视频时长，并自动加淡入（0.5s）+ 淡出（1s），避免突兀。用 `--no-bgm-fade` 可关闭淡变。

**Q13：配音生成的 voiceover_fitted.mp3 文件损坏/无法播放/全是杂音？**
A：常见原因及处理：
1. **极端变速导致杂音**（最常见）：当配音文本很短而视频很长时（如笔记正文主要是 #标签，清理后只剩标题几句话，配音只有 3-4 秒，但视频有 60+ 秒），旧版本会用 atempo 慢放 10-20 倍，导致人声变成低频嗡嗡杂音。**已修复**：当比例 < 0.3 时自动改用"原速播放 + 静音填充"，比例 > 3.0 时截断到视频时长。
2. **偶发文件损坏**：edge-tts 网络波动或 ffmpeg atempo 临时出错。工具已加三层防护：① atempo 处理加 120s 超时；② 处理后用 ffprobe 验证输出文件可解码；③ 验证失败时自动删除损坏文件并回退到原始未变速配音。
3. **建议**：如果笔记正文太短，用 `--voice-text "完整解说词..."` 指定更长的配音文本，让配音时长接近视频时长。

**Q14：ffmpeg 去重超时（"ffmpeg 超时"）？**
A：长视频用默认 `medium` 编码会很慢导致超时。解决：
```bash
# 加快编码速度（推荐，文件略大但画质不变）
python xhs_clone.py "URL" --preset ultrafast

# 同时降低画质要求进一步加速
python xhs_clone.py "URL" --preset ultrafast --crf 23
```
也可永久修改 [config.py:60](file:///d:/xhs/config.py#L60) 的 `VIDEO_DEDUP["preset"]` 为 `fast`（默认已是 fast）或 `ultrafast`。
> 已知问题：之前默认 `medium` + 10 分钟超时对 5 分钟以上的视频不够用，已改为 `fast` + 30 分钟超时。

**Q15：动态水印去除效果不好/字幕被误删？**
A：**推荐改用 OCR 模式**（`--wm-ocr`），能按文字内容区分水印和字幕，不会误删字幕：

```bash
# OCR 模式（推荐）：自动识别 @ 符号水印
python xhs_clone.py "URL" --wm-ocr

# 指定水印文字关键词（更精确）
python xhs_clone.py "URL" --wm-text "@原作者,原创"
```

如果用颜色模式（`--remove-wm`）遇到问题：

| 现象 | 解决方法 |
|------|---------|
| 水印没去掉 | `--wm-threshold` 调高（220），或 `--wm-region` 区域太小 |
| 字幕被误删 | **改用 OCR 模式 `--wm-ocr`**；或 `--wm-region` 限定水印区域 |
| 修复区域有痕迹 | `--wm-dilate 2` 减小修复范围 |
| 追踪失败 | 加 `--wm-no-track` 改每帧检测；或改用 OCR 模式 |

**Q16：动态水印去除太慢？**
A：
- **OCR 模式**：正常速度（OCR 仅采样 10 帧，其余模板匹配很快）。首次运行需下载模型，之后会缓存
- **颜色模式**：加 `--wm-region` 指定区域（追踪模式比自动检测快 5-10 倍）
- 通用：用 `--enhance-only` 处理短片段；或 `--speed 1.5` 减少帧数

**Q17：OCR 模式提示"paddleocr 未安装"？**
A：OCR 模式需要 PaddleOCR，安装：
```bash
pip install paddleocr
```
首次运行会自动下载中文识别模型（约 50MB）。如果不想安装额外依赖，改用颜色模式 `--remove-wm --wm-region "x,y,w,h"`。

---

## 八、项目文件说明

| 文件 | 作用 |
|------|------|
| `xhs_clone.py` | 主程序入口（CLI） |
| `config.py` | 全局配置：路径、去重参数、剪映路径 |
| `core/xhs_parser.py` | 小红书笔记解析与素材下载 |
| `core/douyin_parser.py` | 抖音视频解析与下载（兼容 NoteData 接口） |
| `core/subtitle.py` | 字幕抹除 + ASS 字幕生成 + 自动区域检测 |
| `core/dedup_video.py` | 视频去重（ffmpeg） |
| `core/dedup_image.py` | 图片去重（Pillow） |
| `core/dedup_live.py` | Live 图去重（图+视频） |
| `core/jianying.py` | 剪映草稿生成器 |
| `core/style_enhance.py` | 小红书风格增强 + 二创叠加层 + 字幕处理 + AI配音集成 |
| `core/filters.py` | 滤镜与特效系统（55 滤镜预设 + 44 独立特效：画面/光影/氛围粒子/边框/复古胶片，LUT+原生调色+PNG overlay+geq 粒子） |
| `core/subtitle.py` | 字幕抹除 + ASS 字幕生成 + whisper 识别 |
| `core/tts.py` | AI 配音（edge-tts 神经网络语音合成，26 个中文配音） |
| `core/bgm.py` | 背景音乐（17风格推荐清单/扫描/自动选择/混合） |
| `core/watermark_remove.py` | 动态水印去除（OpenCV 逐帧检测+CSRT 追踪+inpaint 修复） |
| `core/copywrite.py` | 二创文案生成（爆款标题模板库+hook句式+6维评分选优，支持 LLM 模式） |
| `core/cover_gen.py` | 封面生成（4种风格：大字报/底部字幕条/顶部标题条/杂志拼图，支持 LLM 标题 + 原封面去重版） |
| `core/llm.py` | LLM 大模型客户端（统一接口支持豆包/通义千问/DeepSeek/OpenAI，优雅降级） |
| `core/bgm_downloader.py` | BGM 下载助手（从 _sources.json 批量下载免版税音乐） |
| `assets/bgm/` | BGM 素材目录（用户自备音乐，命名：风格_曲名.mp3） |
| `assets/bgm/_sources.json` | BGM 下载源清单（用户填写免版税音乐直链） |
| `assets/luts/` | 15 个 3D LUT `.cube` 文件（滤镜系统用，由 `assets/gen_luts.py` 生成） |
| `assets/overlays/` | 滤镜叠加素材（漏光/粒子/边框/暗角/扫光，由 `assets/gen_overlays.py` 生成） |
| `assets/stickers/` | emoji 贴纸库（二创 overlay 用） |
| `requirements.txt` | 依赖清单 |
| `README.md` | 本操作手册 |

---

## 九、完整操作示例

```bash
# 1. 安装依赖（首次）
cd d:\xhs
pip install -r requirements.txt
pip install edge-tts       # AI 配音（可选）
pip install opencv-python  # 动态水印去除（可选）

# 2. 复刻一条视频笔记
python xhs_clone.py https://www.xiaohongshu.com/explore/abc123def456

# 3. 查看剪映草稿
python xhs_clone.py --list-drafts

# 4. 打开剪映，在草稿里找到 XHS视频_abc123de，编辑/导出

# 5. 直接发布：output/dedup/<ID>/ 下的 video_dedup.mp4 + content_clone.txt

# 6. 带 AI 配音的完整流程（晓晓女声 + 黄字字幕 + 水印）
python xhs_clone.py "URL" --voice xiaoxiao --subtitle-style bold_yellow --watermark "@你的号"

# 7. 配音 + 背景音乐组合（推荐！）
python xhs_clone.py "URL" --voice auto --bgm auto

# 8. 查看全部配音
python xhs_clone.py --list-voices

# 9. 查看 BGM 推荐清单
python xhs_clone.py --list-bgm
```
