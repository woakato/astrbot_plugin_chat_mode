# NovelAI 提示词规则整理（V4.5 / V5）

2026-10-01 整理。来源是官方文档、几个 GitHub 项目和社区实测（链接在文末）。
标【官方】【实测·他人】【经验】的内容是整理来的，标【我们】的是我们自己跑图得到的。
每条规则后面标了出处等级：

- 【官方】NovelAI 官方文档原文
- 【实测·他人】别人在 GitHub 上公开的对照实验（锁种子、只改一处）
- 【经验】社区说法，没有对照实验
- 【我们】我们自己跑图得到的结论

---

## 一、最重要的十条

1. **权重写法是 `1.2::词::`，不是 `(词:1.2)`。** 圆括号在 NAI 里只是普通字符。【官方】
2. **画师名以数字结尾时，写成 `0.7::artist:as109, ::`，逗号必须有。** 数字紧贴 `::` 会被读成权重值（`na_tarapisu153::` 被当成权重 153），整张图会糊成噪点。【实测·他人】
3. **人数标签（`2girls`、`1girl, 1boy`）放主提示词最前面，角色框里只写 `girl` 不带数字。**【官方】
4. **现成角色（模型认识的）在角色框里只写名字，不要再列发色瞳色。** 多写外貌会和名字打架，权重翻倍。原创角色才要把外貌写全。【实测·他人】
5. **姿势、体位、机位、景别用标签；人和人之间的关系用句子。** 长从句描述肢体，模型会把每个从句各画一遍，多出一条腿。【实测·他人】
   我们的补充：双人动作（磨豆腐）光写标签画不对，加一句写清"谁的腿在哪"才对，见第五节。【我们】
6. **露骨概念光写标签不准，要"标签 + 一句具体描述"**（脱肛、扩张、排蛋、道具都是这样）。【我们】
7. **质量标签和负面预设是客户端自己拼上去的，手写一遍就叠了两层。** 二选一：开了 Add Quality Tags 就别手写 `masterpiece` 之类。【官方 + 实测·他人】
8. **Add Quality Tags 会带一个 `no text`；Heavy/Light 负面预设带 `multiple views`（Heavy 还有 `screentone`）。** 出分镜、要文字时这两样都会添乱。【官方】
9. **露骨图用 Full，不用 Curated。** V4.5 Curated 的质量标签里直接带 `rating:general` 和 `-0.8::feet::`。【官方】
10. **Opus 免费出图的条件：单张、不带参考图、普通尺寸以内、步数 ≤28。** 29 步起就扣 Anlas，跟提示词长短无关。【官方】

---

## 二、主提示词结构

### 顺序

官方给的"期望顺序"【官方】：

```
人数（1girl / 2girls / 1boy）→ 角色名 → 作品名 → 其他随意
```

社区对 1844 条真实提示词做的统计（各类词谁在前的比例）【实测·他人】：

```
人数 → 外貌 → 服装 → 表情 → 场景·背景 → 镜头 → 动作 → 质量尾
```

最硬的两条：人数放最前，质量词放最后。V4 以后顺序影响比 V3 小，但重要的东西放前半段仍然有用。【官方】

### 标签和句子怎么分工

V4.5 起标签和自然语言可以混写，V5 对句子理解更好，官方支持英语和日语句子，英语更准。【官方】

| 用标签 | 用句子 |
|---|---|
| 姿势：`sitting` `standing` `lying` `kneeling` `squatting` `on back` `on stomach` `on side` `knees up` `legs up` `leg up` `arm support` | 两个人之间谁对谁做什么 |
| 机位：`from above` `from below` `from side` `from behind` | 东西之间的位置关系、归属 |
| 景别：`close-up` `upper body` `cowboy shot` `full body` | 画面里谁占多大 |
| 视线：`looking at viewer` `looking back` | 光影叙事、进行中的动作状态 |
| 关键物件单独拎出来：`transparent umbrella, wooden bench` | 衣服的不对称状态（只穿一只袜子之类） |

【实测·他人】：姿势写成句子 3/3 画糊，写成标签 3/3 成功；`from below` 写成句子 3/3 没压低机位。

### 互斥组（每组只挑一个）【实测·他人】

| 类别 | 只选一个 |
|---|---|
| 视线 | looking at viewer / looking to the side / looking up / looking down / looking away / looking back |
| 景别 | close-up / portrait / upper body / cowboy shot / full body / wide shot |
| 背景 | simple background / white background / detailed background / transparent background / blurry background |
| 水平机位 | from side / from behind / straight on |
| 垂直机位 | from below / from above（可以和水平机位组合） |
| 版式 | comic / 4koma / multiple views / reference sheet / sticker |

---

## 三、权重语法【官方】

| 写法 | 效果 | 可用版本 |
|---|---|---|
| `{词}` | ×1.05，每多一层再乘一次（`{{词}}` ×1.1025） | 全部 |
| `[词]` | ÷1.05，每多一层再除一次 | 全部 |
| `1.5::词::` | ×1.5，到 `::` 结束 | V4+ |
| `1.5::词A, 词B::` | 一组一起 ×1.5 | V4+ |
| `0.5::词::` | 减弱到一半 | V4+ |
| `-1::hat::` | 定向去掉（比写进负面更准） | V4.5+ |
| `-1::monochrome::` | 概念反转（这个例子会加上颜色） | V4.5+ |

要点：
- **不写结尾的 `::`，权重会一直作用到提示词末尾。** 一个漏掉的 `::` 会让后面全部被加权。
- 单独一个 `::` 会把前面没配对的 `{` `[` 全部关掉。
- **负面框里的 `{}` 是"更要避开"**，方向跟正面相反。

常用数值【实测·他人】：
- 增强常用 1.1 / 1.2 / 1.3 / 1.5 / 2；**1.3 ~ 1.8 是调一个词最值得试的区间**。
- 负权重常用 -1 到 -5，-2 最多。大倍数负权重（如 `-5::...::`）有人报告过整张崩成噪点。
- **默认不加权**，某个元素确实出不来再给它加。

从 ComfyUI / Anima 搬提示词过来要改写法：`(tag:1.2)` → `1.2::tag::`；`\(` `\)` 不需要，直接写圆括号。

---

## 四、画师串

### 写法

- 前缀 `artist:名字` 和直接写名字（`ningen mame`）都有人用；同一个画师两种写法效果不一定一样，第一次用先单画师小图对照。【经验】
- 名字里的空格和下划线：现在是 Qwen 系分词器还是别的，官方没说，不要假设等价。【实测·他人】
- 多画师用**权重梯度**分主次：主导画师 1.0~1.5，辅助的 0.5~0.9 递减。等权平均混合容易互相污染。【经验】
- **数字结尾的画师名一律写 `N::artist:xxx123, ::`**（见第一节第 2 条）。
- 多画师串时 CFG 可以从 3.5~4 起试；单画师从 5~6 起。【经验】
- 画师串和透明背景一起用会出噪点，透明背景只用 ≤2 个画师或者直接写画风词。【实测·他人】

### 别人验收过的 V5 Full 串【实测·他人】

原样复制，不要增删改：

```
社区A（日系赛璐璐、线条清晰）
1.1::artist:ningen_mame::, 0.7::artist:karameru::, 0.6::artist:mery::

清新日常（明亮干净）
0.9::artist:shiromanta::, 0.7::artist:karameru::, 0.6::artist:mignon::

厚涂质感（暖调、油画感）
1.3::chen bin::, 0.8::rella::, 0.6::artist:mx2j::

人体1（裸体人体细节，比例稳、手脚不崩，皮肤偏哑光）
1.2::artist:ask::, 0.7::artist:mignon::

厚涂1（细节密度最高，8 个画师，吃 token）
[artist:cogecha],{{artist:ciloranko}},artist:rella,[ask (askzy)],mignon,artist:kawacy,artist:minaba hideo,[artist:pigeon666]
```

那份资料里还有几条 V4.5 Curated 的串，作者自己标注"画出来都是小孩"，这里没收录。

### 找画师

- [NovelAIv4-Style-Codex](https://github.com/jsh135790/NovelAIv4-Style-Codex)：1000+ 单画师对比图（V4 时代）。
- [nax.moe 画师标签实验](https://nax.moe/?gallery=danbooru-artist-tags-v4.5)：V4.5 下每个画师的固定提示词出图。
- 官方 UI 里输入 `artist:` 会弹出它认识的画师，旁边小圆点越实表示模型越熟。【官方】

---

## 五、多角色【官方】

- 上限：V4/V4.5 最多 6 个角色，**V5 最多 22 个**。
- **主提示词放：人数、场景、画风、动作的总体描述。角色框放：这个角色的外貌。**
- 位置：默认 AI's Choice（从上到下、从左到右排）；改 Custom 可以在画布上拖。API 里是 `center_x / center_y`（0~1），只要有一个角色给了坐标就要把 `use_coords` 设成 true。
- 互动前缀写在角色框里：`source#动作`（主动方）、`target#动作`（被动方）、`mutual#动作`（互相）。
- **每个角色框可以有自己的负面**，用来防两个人特征串色（比如角色 2 的负面写 `black hair`）。
- 不用角色框时，可以在一个框里用 `|` 分隔：`2girls, classroom | girl, black hair | girl, blonde hair`。**两种写法不能混用。**
- **多人图负面里不要放 `extra characters`**，它会和人数标签打架，多出人或者少人。【实测·他人】
- 交互句里用外观指代人（"the girl in the beige coat hands the cup to the girl with headphones"），每个接触点写清是谁的手碰到谁的哪里。【实测·他人】

我们的结果【我们】：蒂法 + 爱丽丝磨豆腐，`tribadism, scissoring` 标签画不出来；在主提示词里加一整句"谁的腿压在谁的大腿上、下身怎么贴"之后姿势对了，两人长相没串。抬起的膝盖会挡住贴合处，要写腿放低、俯视。

---

## 六、特殊标签【官方】

| 标签 | 作用 | 版本 |
|---|---|---|
| `best quality` → `worst quality`（6 档） | 画质 | 全部 |
| `very aesthetic` `aesthetic` `displeasing` `very displeasing` | 好看程度 | 全部 |
| `masterpiece` | 画质 | V4.5+ |
| `year 2014` 这类 | 画风往那一年的流行风格靠 | V2+ |
| `rating:general` / `rating:sensitive` / `rating:questionable` / `rating:explicit` | 分级 | V4.5 Curated 的质量标签里带 `rating:general`，说明认这一套 |
| `low / medium / high / ultra complexity` | 画面复杂度 | V5 |
| `transparent background` `has alpha` `alpha transparency` | 透明背景 | V5 |
| `fur dataset` | 偏兽人画风（放提示词最前） | V4+ |
| `background dataset` | 风景、静物、动物肖像（放最前） | V4.5+ |
| `location` `depthness` `visual novel art` `attractive male` | 其他 V5 新增 | V5 |

`high complexity` 有人报告会让画面固化，默认不加。【实测·他人】

---

## 七、质量标签和负面预设原文【官方】

### Add Quality Tags 加在末尾的内容

| 模型 | 追加 |
|---|---|
| V5 Full / Curated（Standard） | `, very aesthetic, masterpiece, no text` |
| V5 Full / Curated（Light） | `, very aesthetic, amazing quality, no text` |
| V4.5 Full | `, location, very aesthetic, masterpiece, no text` |
| V4.5 Curated | `, location, masterpiece, no text, -0.8::feet::, rating:general` |

### 负面预设

**V5 Heavy**（V4.5 Full 的 Heavy 同样内容）：
```
lowres, artistic error, film grain, scan artifacts, worst quality, bad quality, jpeg artifacts, very displeasing, chromatic aberration, dithering, halftone, screentone, multiple views, logo, too many watermarks, negative space, blank page
```

**V5 Light**：
```
lowres, bad hands, bad anatomy, artistic error, sepia, white haze, worst quality, very displeasing, jpeg artifacts, 0::ai-generated::
```

**V5 Human Focus**：Heavy 全部 + `@_@, mismatched pupils, glowing eyes, bad anatomy`


---

## 八、文字渲染【官方】

- 正面加 `text, english text`（中文写 `chinese text`），然后在**主提示词最末尾**写 `Text: 要显示的字`。`Text:` 后面再写别的词会被一起画进图里。
- 多段文字之间空一行（网页里 Shift+Enter）。
- 字数上限（含空格换行）：V4/V4.5 **118**，V5 Curated **374**，V5 Full **750**。
- 语言：V5 支持英文、日文、中文等；V4/V4.5 只有英文。**英日最好，中文偏弱。**
- 文字出不来先关掉 Add Quality Tags（`no text` 在压它）。
- 网页版里用英文双引号括起来的字会自动生成 `Text:`；手写了 `Text:` 就关掉这个自动功能。所以**普通叙述句不要整段加引号**。【实测·他人】
- 引号里的字要配一个载体（气泡、纸条、招牌）并写清位置，否则字会乱飘。【实测·他人】

---

## 九、参数

| 参数 | 建议 | 出处 |
|---|---|---|
| 步数 | 先低步数找构图，满意了再 Enhance；Opus 免费上限 28 | 【官方】 |
| Guidance（scale） | V3 起建议 5~6；低更柔和、高更锐 | 【官方】 |
| 采样器 | 推荐 Euler Ancestral 或 DPM++ 2M | 【官方】 |
| Noise schedule | 1844 条里 98% 用 karras | 【实测·他人】 |
| CFG rescale | 0（41% 的人用 0） | 【实测·他人】 |
| Variety+ | API 字段 `skip_cfg_above_sigma`（UI 开关对应 58），构图更多样 | 【经验】 |
| SMEA / DYN | 大图保持一致性的旧采样参数；**不要往 V5 发** | 【官方 + 实测·他人】 |
| 尺寸 | 竖 832×1216、横 1216×832、方 1024×1024 | 【官方】 |
| 提示词长度 | V5 Full 约 1471 token、Curated 约 703（软上限，超了也能出图） | 【实测·他人】 |

**10-03 我们实测：参数不是让它更听话的杠杆**（雷电将军乳环 + 阴蒂环 + 三分叉铁链，定版提示词不动，同 8 个种子只改一个参数，rescale 都是 0.1）：
引导 5 → 2/8、**8 → 6/8**、10 → 4/8；步数 23 → 5/8、28 → 6/8，而且 23 步和 28 步同一个种子的构图几乎一样。
构图（东西放哪、怎么连）在前几步就定了，加步数只补细节；超过 28 步还要花 Anlas（V5 Full 竖图 40 步约 36、50 步约 43 一张）。
引导调低更不听话（多出来的链、接错地方都回来了），调高画面更硬也没更听话。上面官方「V3 起建议 5~6」说的是画面观感，结构复杂的需求留在 8。要它听话，改写法。【我们】

---

## 十、API 能改的东西

API 请求体（V5 `params_version: 4`，V4/V4.5 是 3）里能调的：

| 字段 | 作用 |
|---|---|
| `v4_prompt.caption.base_caption` | 主提示词 |
| `v4_prompt.caption.char_captions[]` | 角色框，每项 `{char_caption, centers:[{x,y}]}` |
| `v4_negative_prompt.caption.char_captions[]` | 每个角色自己的负面 |
| `v4_prompt.use_coords` | 是否按坐标摆角色 |
| `scale` / `cfg_rescale` / `steps` / `sampler` / `seed` | 基本参数 |
| `noise_schedule` | karras / exponential / polyexponential |
| `skip_cfg_above_sigma` | Variety+（UI 开关对应 58） |
| `qualityToggle` / `ucPreset` | 质量标签、负面预设 |
| 订阅接口里的 V5 剩余额度 | 出图前检查电量（别人的 ComfyUI 节点会在电量低于 2% 时拒绝出图） |

自己写调用 NAI 的小工具时，值得做一个**免费条件守卫**：出图前确认步数 ≤28、尺寸在普通以内、单张，不满足就提示会扣 Anlas；V5 再查一下剩余电量。同账号并发会回 429，每张之间等几秒、遇 429 重试。

---

## 十一、说法冲突和没核实的地方

- **Vibe Transfer（画风迁移）V5 能不能用**：官方 V5 发布公告说上线时没有；有人 09-14 实测说已经能用，按 Anlas 计费。没核实。
- **Precise Reference（参考图保持角色）**：官方文档写的是只有 V4.5 能用，V5 还没有。要靠参考图让原创角色稳定，现在只能用 V4.5。
- **`censored` 放负面会不会反而打码**：有一份资料这么说，但按负面的原理是"避开"，我们这两天把它放在负面里一直没出马赛克。按我们的经验保留。【我们】
- **"1.5 在 V5 有负权重效果"**：只有一份资料提到，没有给证据，存疑。
- **画师名空格和下划线是否等价、`artist:` 前缀加不加效果是否一样**：没有官方说法，第一次用某个画师要自己对照。

---

## 十二、从中文需求写提示词：四条补充（10-02）

看了 6 个 AstrBot 上的 NAI 生图插件（nai_brush、nai_pic、nai_plus、nai_prompt、novelai_painter、nai_draw），把它们做得好的四件事收进来。

### 1. 中文里的强调词 → 权重【别人的做法】

用户用中文说"突出 / 强调"时，给对应那个词加权；**他没强调的一律不加权**（第三节"默认不加权"不变）。

| 用户怎么说 | 写法 |
|---|---|
| 突出、强调、重点、要明显 | `1.2::词::` |
| 非常、极其、最、一定要、特别 | `1.5::词::` |
| 弱化、淡化、稍微、一点点、隐约 | `0.8::词::` |
| 不要、去掉、别有 | **不是减权重**：正文写 `-1::词::` 或放进负面（第一节） |

- 一句话里强调的是一整组东西（"重点是灯光和倒影"），用组权重 `1.2::词A, 词B::`。官方支持组权重，有的插件规定不许用，那是它们自己的限制。
- 加完权重要跑 lint 脚本：漏掉结尾 `::` 会把后面全部加权。
- nai_prompt 插件把"不要"也映射成 0.8，这是错的，别照抄。

### 2. 现成角色名照 NAI 官网的联想写【官方 + 我们】

- 写法跟 NAI 网页输入框弹出的联想一致：**danbooru 标签，下划线换成空格，作品名括号照官网原样**。例：`kisaki (blue archive)`、`hatsune miku`（不带括号）、`aemeath (wuthering waves)`。
- 括号只在标签本身就带括号时才加。不要自己给不带括号的角色补作品名（`hatsune miku (vocaloid)` 是错的）。
- 联想接口：`GET https://image.novelai.net/ai/generate-image/suggest-tags?model=<模型>&prompt=<前缀>`，返回 `{"tags":[{"tag","count","confidence"}]}`，不扣点、很快（实测）。
- ⭐**联想只是参考，不是门槛**：有些标签联想里没有，NAI 照样画得出来。联想没有的名字照写，只在回复里提一句"这个名字 NAI 联想里没有，画不对就改成外貌描述"。

### 3. 标签有没有、要不要核对【别人的做法】

- 有的插件出完提示词会自动核对每个标签存不存在。我们的做法：**角色名、作品名、生僻概念**才去核对联想（第 2 条的接口）；常见标签（`sitting`、`smile`、`beach`）不查。
- 查不到不代表不能用（同上，联想只是参考）。查不到的概念，按第一节第 6 条改成"标签 + 一句具体描述"更稳。

### 4. 连续修改：三档继承【别人的做法，给 QQ 这类聊天工具用】

在聊天里用户说"再来一张""换个姿势""换成别人"时，先判断属于哪一档，再改上一版提示词：

| 档 | 用户的话像什么 | 怎么改 |
|---|---|---|
| A 微调 | "把背景换成晚上""加个帽子""表情害羞一点""胸小一点" | 上一版整份保留，**只改他说的那一处**，没提的标签一个都不动 |
| B 换人保场景 | "换成另一个角色""换成某某" | 场景、构图、光照、机位、氛围保留；角色名、外貌、服装、身材换掉 |
| C 全新 | 跟上一版没关系的新要求 | 上一版全部丢掉，从头写 |

- 判断不出来时按 A 处理，并在回复里说一句"我按微调改的，想全换就说"。
- 只改了几处时，回复里列出改了哪几处（第 7 步"变体只说换哪一句"同理）。
- 用户看图后说"不满意"但没说哪里不满意：先问一句是构图、人物、画风还是细节，别整份重写。

---

## 十三、横图别让耳朵顶出上沿【我们，10-11】

1216×832 横图画人，头常常顶到上沿，兽耳被切平。要留出头顶，**写清耳朵上面那一条画什么具体东西**：
- 卧室：`Her head is in the middle of the frame, not near the top: above the tips of her fox ears there is a wide strip of the bedroom wall with the headboard and a string of fairy lights.`
- 窗边站着全身：`She is seen from across the room and takes up about two thirds of the frame height: above the tips of her fox ears there is a wide strip of the bright window and the curtain rail.`

同请求体、换这一句的对照（三种构图 + 被子构图，耳朵上面空出 12 像素以上才算）：原样 1/7；具体那句 8/9；只说 `a wide strip of the room behind her` 5/8（被子构图那张反而顶到了）；「整个头和耳朵都在画面里，上面留一条空」+ 负面 `head out of frame, ears out of frame, cropped head` 3/9。
角色框中心往下放（`position: custom`，y 0.7）9/9 不顶，但人缩得很小、躺姿变形，不用。
侧躺时横着伸出去的那只耳朵，这一句管不了（会碰到左右两边）。

## 来源

官方：
- [Image Generation Basics](https://docs.novelai.net/en/image/basics/)
- [Tagging](https://docs.novelai.net/en/image/tags/)
- [Strengthening & Weakening](https://docs.novelai.net/en/image/strengthening-weakening/)
- [Add Quality Tags](https://docs.novelai.net/en/image/qualitytags/)
- [Undesired Content](https://docs.novelai.net/en/image/undesiredcontent/)
- [Multi-Character Prompting](https://docs.novelai.net/en/image/multiplecharacters/)
- [Text Rendering](https://docs.novelai.net/en/image/textrendering/)
- [Steps & Guidance](https://docs.novelai.net/en/image/stepsguidance/)
- [Sampling](https://docs.novelai.net/en/image/sampling/)
- [Prompt Mixing](https://docs.novelai.net/en/image/promptmixing/)（只有 V3 及以下可用）
- [Models](https://docs.novelai.net/en/image/models/)

GitHub：
- [2332239652/novelai-assistant](https://github.com/2332239652/novelai-assistant)（V5 实测校正、已验收画师串、分镜规则）
- [Miint-Sunny/nai5-prompting](https://github.com/Miint-Sunny/nai5-prompting)（1844 条真实提示词统计 + 锁种子对照，上一个仓库的实测数据来源）
- [cheng1559/novelai-agent](https://github.com/cheng1559/novelai-agent)（V5 语法速查）
- [raspie10032/ComfyUI_RS_NAI_API_Request](https://github.com/raspie10032/ComfyUI_RS_NAI_API_Request)（API 字段、V5 电量检查、权重写法转换）
- [LlmKira/novelai-python](https://github.com/LlmKira/novelai-python)（Python SDK，请求体结构）
- [jsh135790/NovelAIv4-Style-Codex](https://github.com/jsh135790/NovelAIv4-Style-Codex)（画师对比）
- [sanqianlideyunheyue-lang/novelai-image-tools-guide](https://github.com/sanqianlideyunheyue-lang/novelai-image-tools-guide)（中文工具笔记）

AstrBot 插件（10-02 参考，只借了做法，没装）：
- [saberlights/nai_pic_plugin](https://github.com/saberlights/nai_pic_plugin)（三档继承）
- [cklmdoc/astrbot_plugin_nai_prompt](https://github.com/cklmdoc/astrbot_plugin_nai_prompt)（中文强调词 → 权重档位）
- [Shawlei/astrbot_plugin_nai_plus](https://github.com/Shawlei/astrbot_plugin_nai_plus)（角色标签只照原样加括号）
- [exynos967/astrbot_plugin_nai_brush](https://github.com/exynos967/astrbot_plugin_nai_brush)、[zzz27578/astrbot_plugin_novelai_painter](https://github.com/zzz27578/astrbot_plugin_novelai_painter)、[TsoiTZF/astrbot_plugin_nai_draw](https://github.com/TsoiTZF/astrbot_plugin_nai_draw)
