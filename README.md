# jobfxxk

把秋招投递从头做到尾的 Claude skill。

给它一份简历或者几段口述，它帮你整理成信息库、按 JD 微调简历（**保证版式和你原来的一模一样**）、从一万多条岗位里筛出匹配的、打开招聘官网把表单填好，最后记进投递台账。

**提交按钮永远由你自己按。**

---

## 它能干什么

- 把你的简历（PDF / Word / 截图 / 口述）拆成结构化条目，逐条和你核对，数字逐个确认
- 按 JD 改写措辞，**只改说法不改事实**，字数控制在版面能容下的范围，导出后版式零位移
- 从实时更新的岗位库（约 12000 条）里按届次、学历、城市、方向筛岗
- 后台读目标岗位的 JD，不在你屏幕上开页面
- 打开招聘官网，把姓名、教育、实习、项目这些文字内容填进去
- 把每次投递记进台账

## 它不干什么

- 不替你点提交、保存、确定
- 不编造经历，不改你简历上的任何数字
- 不把你的信息上传到任何地方

---

## 安装

### 方式一：保存成 skill（Claude 桌面版）

下载 Release 里的 `jobfxxk.skill`，双击或在应用里打开，点「保存技能」。

### 方式二：从 GitHub 克隆（Claude Code）

```bash
cd ~/.claude/skills
git clone https://github.com/jinyu4619-cmd/jobfxxk.git
```

### 方式三：只用脚本

把仓库解压到任意目录，直接跑 `python scripts/setup.py --check`，不需要装成 skill。

### 需要什么

- Windows 10 / 11（Mac 和 Linux 还没适配）
- Microsoft Word，导出 PDF 用；WPS 多数版本也行
- Node.js 20 以上
- Python 3.9 以上
- 一份简历的 **Word 版和 PDF 版**

Playwright、pypdf 这些依赖不用手动装，第一次跑会提示并代你装好。

---

## 第一次使用：说一句「开始」

装好之后，跟它说「开始」或者「帮我投简历」。

它会自己检查数据目录、配置文件、简历、依赖、版面标定、岗位库。**缺什么问什么，一次问一件**，答完继续往下走。你不用改任何文件，也不用记命令。

会问到的大致就这几种：

| 它发现 | 它会说 |
|---|---|
| 没装 Node | 「先装 Node，装完跟我说一声」 |
| 没有简历 | 「把 Word 版和 PDF 版各给我一份」 |
| 只有 PDF | 「只有 PDF 改不了字，先用内置模板起步？」 |
| 缺填表组件 | 「还差一个组件，约 10MB，要装吗」 |
| 没标定版面 | 「量一下行宽，十几秒」 |
| 岗位库没同步 | 「同步岗位库，大概一分半，别关窗口」 |

全部就绪后，它会说「可以开始了。你想投什么岗位？」

### 为什么非要两个版本

- **docx** 用来改字：所有按 JD 的微调都在这份上原地替换，不进版式层，所以改完和原来一模一样
- **pdf** 用来量版式：每个位置能放多少字，是从 PDF 的实际排版反推出来的

只有 PDF 不是不能投，是没法自动微调。这种情况用 `assets/template/` 里的内置模板起步，或者你自己用 Word 排一份。

### 数据放哪

个人数据不在仓库里。配置、锚点表、岗位缓存默认放用户主目录下的 `jobfxxk-data/`；如果你是 `git clone` 的，就放在与仓库平级的 `jobfxxk-data/`。**代码升级不会覆盖你的数据。**

---

## 完整跑一遍

下面是整条链路涉及的全部命令。**正常情况下你不需要逐条敲**——说一句「开始」，agent 会通过这些脚本自己推进。这一节是给想手动控制的用户看的。

### 1. 选浏览器（可以跳过）

```bash
python scripts/detect_browsers.py
```

不选也能用：默认按 `config.browser_path` → 系统自带 Edge → Chrome → Playwright 内置内核的顺序试。想固定用某个浏览器，把它写进 `config.json` 的 `browser_name` / `browser_path` 就行。

### 2. 同步岗位库（每天第一次用）

```bash
node scripts/sync_jobs.cjs --only=jobs      # 约 90 秒，抓 12000 条
python scripts/translate_jobs.py            # 把选项 ID 翻成中文，约 40 秒
```

缓存默认写到 `config.job_cache`，筛岗和翻译读的是同一个位置。

### 3. 筛岗

```bash
python scripts/filter_jobs.py --city 杭州 --degree 硕士 --year 2027 --keyword 运营 --top 6
```

输出前 6 条，带公司、岗位、城市、截止日期、投递链接。清单同时存到 `jobfxxk-data/shortlist.json`。

不确定有哪些城市可选：

```bash
python scripts/filter_jobs.py --list-cities
```

### 4. 读 JD

```bash
node scripts/fetch_jd.cjs "岗位详情页链接"
```

输出了岗位职责和任职要求。读不到它会明说，不会瞎猜。

### 5. 标定模板（换模板时才需要重跑）

```bash
python scripts/make_anchors.py
```

量出每行能放多少字、每个位置的字数窗口，写到 `jobfxxk-data/user/锚点表.json`。

### 6. 微调简历

先写一份草稿 `draft.json`：

```json
[{"old": "原文片段", "new": "替换后文本", "why": "对应 JD 的哪个词"}]
```

然后：

```bash
python scripts/fill_engine.py draft.json
```

引擎会校验字数窗口 → 拒绝超窗条目 → Word 原地替换 → 导出 PDF → 回搜验证替换真的生效 → 复验页数/首行/末行/行数。

### 7. 投递

```bash
node scripts/browser.cjs launch "岗位链接"     # 开窗口，等用户扫码登录
node scripts/browser.cjs dump                 # 看有哪些字段
node scripts/browser.cjs upload "简历.pdf"    # 上传附件
node scripts/browser.cjs fill fill.json       # 填文字框
node scripts/browser.cjs shot now.png         # 截图给用户看
node scripts/browser.cjs close                # 关掉自动化浏览器（不影响你日常那个）
```

**下拉、日期、单选留给用户点。提交按钮也由用户按。**

### 8. 记账

```bash
python scripts/write_tracker.py --company "某公司" --position "用户运营管培生" \
  --city 杭州 --resume 微调版 --link "https://..."
```

追加一行并重算统计。状态变了不要手改表格：

```bash
python scripts/write_tracker.py --update 3 --status 面试中 --next "10/28 二面"
python scripts/write_tracker.py --list
```

---

## 目录结构

```
jobfxxk/
├── SKILL.md                  主流程（11 个阶段）
├── references/
│   ├── 排版规则.md            版式硬指标、模板标定、打印适配
│   ├── 微调细则.md            按 JD 微调简历的规则与验收
│   ├── 信息库规范.md          经历的结构化与逐条确认
│   ├── 问题库.md              网申字段清单、隐私分级
│   ├── 岗位推荐.md            数据源、匹配打分、意向获取
│   └── 投递台账.md            进度跟踪表
├── scripts/                  17 个脚本（见下表）
├── assets/template/          脱敏简历模板（docx + pdf，必须成套）
├── config.example.json
└── package.json
```

| 脚本 | 作用 |
|---|---|
| `setup.py` | **启动入口**：检查状态、报告卡点、执行修复 |
| `state.py` | 读写进度状态，断了能接上 |
| `profile.py` | 个人档案：静态信息 + 经历库，只问缺的 |
| `check_config.py` | 逐项检查配置与环境（setup.py 的详细版） |
| `detect_browsers.py` | 扫描本机浏览器 |
| `secure_store.py` | 敏感信息 DPAPI 加密存取 |
| `make_anchors.py` | 标定模板，算字数窗口 |
| `sync_jobs.cjs` | 同步岗位库 |
| `translate_jobs.py` | 选项 ID 翻译成中文 |
| `filter_jobs.py` | 按条件筛岗 |
| `fetch_jd.cjs` | 后台读 JD |
| `browser.cjs` | 浏览器：launch / pages / dump / fill / upload / text / shot / close |
| `fill_engine.py` | 窗口校验 + Word 替换 + 版面复验 |
| `write_tracker.py` | 写投递台账、改状态 |
| `paths.py` | 统一的路径解析 |
| `config.cjs` | Node 侧的配置读取（与 paths.py 同一套规则） |
| `browser_config.cjs` | 统一的浏览器选择与启动 |

---

## 隐私

个人数据全部放在与仓库平级的 `jobfxxk-data/`，**物理上不在仓库里**。

身份证、家人电话这类敏感信息存在 `sensitive.bin`，用 **Windows DPAPI** 加密——钥匙绑在你的 Windows 账户上，不落地任何密钥文件。文件拷到别的电脑、或换个 Windows 账户都解不开。

边界要说清：如果电脑中了木马，或者别人能登进你的 Windows 账户，任何本地加密都挡不住。

---

## 已知限制

- **只支持 Windows + Word**。导出 PDF 走的是 Word 的 COM 接口，Mac、Linux、没装 Office 的机器跑到这一步会停。后面会加 LibreOffice 兜底
- **必须 docx 和 pdf 成对**。只有 PDF 的用户没法自动微调，只能用内置模板起步，或者自己用 Word 排一份
- **岗位库是国内校招表**，覆盖 2027 届应届生岗位。社招、海外用户拿到这份表筛不出东西
- **验证码、滑块**必须你自己点，自动化过不去
- **JD 读取**按系统适配：北森系最稳（走它自己的接口），其他系统退回读页面正文，读不到会明说
- **内置模板是固定骨架**（3 段实习 / 2 个项目 / 1 段校园）。装不下你的经历时，用 Word 打开 `assets/template/简历模板.docx` 自己改条目
- **岗位库是别人维护的公开表格**，时效性由维护者决定，投递前请自己确认岗位还开着

---

## 常见问题

**Q：跑 `node scripts/xxx.cjs` 报 `Cannot find module 'playwright'`**
在 `jobfxxk` 目录里执行 `npm install`。

**Q：`check_config.py` 说找不到 config.json**
它应该在 `jobfxxk-data/config.json`，不在仓库里。按「第一次配置」三步走一遍。

**Q：微调后版面变了**
说明改写后的字数超出了该位置的字数窗口。看上一步输出里被标 `✗` 的条目，把它改回窗口内。

**Q：`browser.cjs` 说没有可用的自动化浏览器**
先 `node scripts/browser.cjs launch <链接>` 开窗口，再执行其他命令。
