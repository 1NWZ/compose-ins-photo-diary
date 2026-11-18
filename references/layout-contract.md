# 布局协议 v1

本协议与 `scripts/compose_ins_diary.py` 配套。它是本重建版定义的格式，不声称与截图原项目的未知 JSON 格式兼容。

## 文件与路径

- `sources.json` 由 `inspect` 生成，包含 3–6 个唯一 ID、文件路径、SHA-256、EXIF 校正后的尺寸和颜色处理记录。不要为通过校验而篡改清单。
- 清单中的相对路径以 **清单所在目录** 为基准；背景路径以 **layout.json 所在目录** 为基准。命令行输出路径以当前工作目录为基准；绝对路径也支持。
- 字体始终来自 Skill 自身的 `assets/fonts/`，无需用户指定系统字体。运行脚本不联网，不下载素材，也不调用生图接口。
- `direction.json` 记录语义分析、背景候选和审美选择，由 Agent 维护；渲染器不把主观分数当成机器检测结果。

## 顶层字段

| 字段 | 格式 | 含义 |
|---|---|---|
| `schema_version` | 整数 `1` | 本版协议 |
| `canvas` | `[1536, 2048]` | 最终画布固定尺寸 |
| `mode` | 三种模式之一 | 决定主字体，几何布局由 Agent 编写 |
| `background` | 对象 | 已保存的生成背景，见下文 |
| `photos` | 3–6 个对象 | 必须覆盖清单中全部 ID，每个一次 |
| `texts` | 1–24 个对象 | 英文栏目词、短句、连接词与功能性括号 |

未定义字段会报错，避免拼写错误静默失效。JSON 重复键也会报错。

### 背景

必填：`path`、原文件字节的 `sha256`、`kind: "generated"`、实际使用的非空 `prompt`。图片至少宽 768 px、高 1024 px，必须不透明。非 3:4 背景居中等比裁切到画布，不拉伸。

`kind` 是调用方声明，不是来源鉴定。脚本可拦截与任一源图字节相同或规范化像素相同的背景，但不能识别源图的所有变形副本；Agent 必须执行“纯文字独立生背景”的隔离规则。

### 照片

| 字段 | 格式 | 说明 |
|---|---|---|
| `source_id` | 清单中 ID | 不直接在布局中指定替换图片路径 |
| `box` | `[x, y, width, height]` | 整数像素，左上角原点；旋转前的区域 |
| `focus` | `[fx, fy]` | 0–1，EXIF 校正后图片中的视觉焦点 |
| `protected_region` | `[left, top, right, bottom]` 或 `null` | 0–1，必须完整保留的主体范围；多个关键主体取联合框；没有关键主体时明确填 `null` |
| `direction_sensitive` | 布尔值 | 人、建筑、可读文字等为 `true` |
| `rotation` | -6 到 6，可省略 | 正数逆时针，默认 0；方向敏感照片只能为 0 |

渲染按 `cover` 等比裁切：先以焦点为中心，再将裁切框移到能完整容纳保护区的位置。装不下保护区时直接报错；应调整 `box` 宽高比或重新布局，不应缩小保护框来掩盖裁切。

原图的 ICC 色彩管理与 EXIF 校正不属于审美调色。无 ICC 的 RGB/灰度图按 sRGB 解释；带 ICC 的图转换为 sRGB；无 ICC 的 CMYK/LAB 会报错。PNG/WebP 透明通道保留。裁切与缩放会改变像素数量，因此“保留原图”指内容来源和有限变换，不指成片像素与原图逐字节相同。

### 文字

必填 `text`、`box`、`font_size`、`color`，可选 `min_font_size`、`align`、`role`。

- `text`：1–240 字符，支持可打印 ASCII 和换行。当前包不支持中文排字、emoji、弯引号或特殊符号；英文撇号、连字符及普通括号可用。
- `box`：与图片相同的 `[x,y,w,h]`，容纳区域而非背景矩形。文字无底色。
- `font_size`：24–200 的整数；`min_font_size` 默认相同。若给定更小最小值，脚本逐像素减字号直到能容纳；最小仍放不下则报错，不截断内容。
- `color`：`#RRGGBB`；`align`：`left`（默认）、`center`、`right`。垂直顶端对齐。
- `role`：`label`（默认）或 `bracket` 使用模式主字体；`caption` 使用 Inter 400。`place-note` 主字体是 Inter 300，`quiet-journal` 主字体是 Cormorant Garamond 400。
- 括号可直接写在文字内，例如 `( objects )`；也可作为单独 `role: "bracket"` 的元素组成分组。不使用沿照片四边的装饰框。

## 约束与排错

- 所有实际绘制范围必须处于 64 px 安全边距内；旋转后的完整外接矩形也必须满足。
- 图片短边至少 120 px；照片与照片、文字与图片、文字与文字的外接矩形不能重叠。脚本采用保守外接矩形，轻微旋转后应预留更多空隙。
- 至少 35% 画布不被前景外接矩形占用。这只是几何留白，不代表背景已经足够安静。
- 源文件在建清单后发生变化会报哈希不匹配。只有用户确认更换素材后才重建清单。
- 同一份文件重复出现会报错。相似照片或像素相同但字节不同的文件需要 Agent 根据真实输入判断；不要擅自去重。
- 不覆盖输入照片、背景、布局、清单、所用字体或 sRGB 配置文件。输出只支持 PNG。
- 所有最终验收还包括 Agent 看图：事实准确性、背景是否合规、保护框是否选对、文字与背景对比度等不由脚本自动证明。

## 最小完整布局示例

下例说明字段与几何关系，**不是固定模板，也不是可直接运行的素材包**。`sha256` 必须填入实际背景的 64 位十六进制摘要；图 ID 应来自实际 `sources.json`。文件名仅示意。

```json
{
  "schema_version": 1,
  "canvas": [1536, 2048],
  "mode": "quiet-journal",
  "background": {
    "path": "background-a.png",
    "sha256": "实际背景的SHA-256",
    "kind": "generated",
    "prompt": "完整实际纯背景提示词，不附加任何用户照片"
  },
  "photos": [
    {"source_id": "photo-01", "box": [380, 740, 310, 310], "focus": [0.5, 0.5], "protected_region": [0.25, 0.25, 0.75, 0.75], "direction_sensitive": true},
    {"source_id": "photo-02", "box": [740, 750, 370, 280], "focus": [0.5, 0.5], "protected_region": null, "direction_sensitive": false, "rotation": -2},
    {"source_id": "photo-03", "box": [610, 1150, 300, 330], "focus": [0.5, 0.5], "protected_region": [0.3, 0.3, 0.7, 0.7], "direction_sensitive": true}
  ],
  "texts": [
    {"text": "small pauses", "box": [350, 570, 836, 100], "font_size": 84, "color": "#263A32", "align": "center"},
    {"text": "( ordinary things )", "box": [430, 1560, 676, 70], "font_size": 40, "color": "#263A32", "align": "center"}
  ]
}
```

计算真实背景的摘要，可运行：

```bash
python3 -c "import hashlib,pathlib; print(hashlib.sha256(pathlib.Path('background-a.png').read_bytes()).hexdigest())"
```

## 输出与复现

正式 PNG 为 1536×2048，预览为 768×1024。预览先以正式尺寸合成再缩小，布局坐标始终采用正式尺寸。

与 PNG 同名的 `.provenance.json` 记录源图 SHA-256、规范化像素摘要、EXIF/ICC 处理、裁切框、缩放尺寸、旋转、放置区域、字体摘要、背景摘要、输入配置摘要和最终图片摘要。它不复制照片的 EXIF、GPS 或本机绝对路径。实际生成提示词仍可能含用户提供的信息，按工作文件处理。

相同输入、布局、字体和相同 Pillow/FreeType/色彩管理运行环境下，像素结果可复现；跨版本编码器与字体栅格化差异可能改变文件摘要。包中固定的 `assets/srgb.icc` 避免每次创建 ICC 配置时写入不同生成时间。

SHA-256 用于本地来源追踪，不是数字签名、版权证明或对外部篡改的独立认证。
