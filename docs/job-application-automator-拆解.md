# job-application-automator 拆解与可行性判断

对象：PyPI `job-application-automator` 1.2.7（63 KB whl，四个源文件，约 5 700 行 Python）。
结论先说：**整体移植不划算，能借的是结构不是代码。**下面是依据。

## 一、它到底是什么

一句话：**Greenhouse 专用的表单提取器 + 一套通用填充器 + 一个 MCP 外壳。**

`form_extractor.py` 第一行自己写着 `Simplified Greenhouse Form Extractor`。整个包里唯一"聪明"的部分——iframe 探测、表单页定位、标签解析——都是照着 Greenhouse 的 DOM 形状写的，Workday / Lever 只是顺带认了个名字（`_extract_domain_from_src`，form_extractor.py:2928）。

技术栈：Playwright + `undetected-playwright`（stealth，headless=False）+ BeautifulSoup/lxml + geocoder，包成 `mcp>=1.11` 的 server，暴露五个工具：`simple_form_extraction`、`simple_form_filling`、`create_cover_letter`、`get_applied_jobs`、`health_check`（mcp_server.py:68 / 243 / 456 / 544 / 746）。

## 二、它的流程

```
extract_form_data(url)
  → {url, form_context, user_input_template:[{id,type,label,required,options...}]}
  → 模型/用户把 user_input_template 填上值
  → fill_all_fields(form_data)
  → 停下来等人自己点提交
```

填完之后 `wait_for_user_review_and_submit`（form_filler.py:885），自己从不点提交。

这条路线跟 jobfxxk 现在这条路（`dump` → `fill_plan` → `fill` → 用户点提交）是同一套骨架。所以它验证的是"这条路对"，而不是提供了一条新路。

## 三、值得借的三点

1. **按控件类型分头抽取。** 它有六个独立的抽取器：电话（1447）、文本（1575）、下拉（1754）、文件（1915）、多行文本（2063）、复选框组（679），每类单独处理、单独返回。jobfxxk 现在是 `dump` 一把梭，混在一起。
2. **文件字段抽得细。** `_extract_accepted_types`（2651）会读 accept 白名单，`_extract_upload_options`（2618）会把"从设备上传 / 从网盘选"这类入口也记下来。国内表单上传简历是必经一步，这块信息现在 jobfxxk 完全没有。
3. **复选框组按组判断必填**（`_is_checkbox_group_required`，1191）。jobfxxk 今天刚由 Codex 补上同类逻辑（page_ops.cjs 的 group audit），已经不缺了。

## 四、不能用的部分（占八成）

| 它的做法 | 换到国内为什么不行 |
|---|---|
| Greenhouse / Workday iframe 探测，一千多行（form_extractor.py:1287–1360） | 北森 `zhiye.com`、Moka `app.mokahr.com`、娃哈哈自建站，表单都在主文档里，不进 iframe |
| `_is_valid_label` 要求"至少两个单词 / 全大写 / 带问号"（2696） | 中文标签「姓名」「籍贯」「期望薪水(元)」全部会被判成无效标签 |
| 全库没有日期控件处理（搜 `date/month/year/calendar` 零命中） | 国内的出生日期、教育起止、实习起止几乎全是 `el-date-picker`，占了表单最费时的一块 |
| 没有级联处理 | 籍贯、现居住地、期望城市全是省市级联，选完上一级要重建下一级 |
| 自定义下拉走 `click → type → Enter`（form_filler.py:626–656） | 对 element-ui / antd 的浮层基本无效；jobfxxk 的 `pick_logic`（开浮层 → 读完所有选项 → 唯一命中才点 → 回读）比它强 |
| 定位字段优先用 `get_by_role('textbox', name=label)`（481） | 中文表单的 label 大多没和控件做 ARIA 关联，退回 `#id` 选择器；而北森/Moka 的 id 是随机生成的，页面一重渲染就失效 |
| `undetected-playwright` 反检测 + `geocoder` 地址联想 | 两个都有反效果：前者会和国内站点自己的风控打架；后者走 Google/OSM，境内不可达 |

## 五、接进来要付多少

如果真要做成"适合国内 ATS"，等价于：

- **抽取层重写**：中文标签规则、`el-select`／`antd` 浮层选项、日期控件、级联控件。这一项就是 jobfxxk 的 `page_ops.cjs` 加 `pick_logic.cjs` 的全部内容。
- **填充层换定位策略**：从"按 id 找"换成"按容器文字找控件"，否则页面重渲染一次就全失效。
- **MCP 外壳重写**：它是自己的 MCP server，跟 jobfxxk 的脚本体系是两套东西，胶水要另写。
- **砍依赖**：`undetected-playwright`、`geocoder`、`beautifulsoup4` 都用不上。

工作量不少于现在 jobfxxk 已有部分的合计，而能借的只有目录结构和三个小函数。

## 六、判断

**不接。** 理由三条：

1. 它的价值在 Greenhouse 的 DOM 细节上，而 Greenhouse 不在你的投递范围里。
2. 它缺的（日期、级联、中文标签、浮层下拉）恰好是国内表单最费时的四块，而这四块 jobfxxk 已经做到一半以上。
3. 填得慢的根因不在这里——今天实测：每条命令 `require('playwright')` 2.90 秒、连 CDP 1–2 秒，一张表拆成十几条命令，光启动开销就三十秒以上。这是 jobfxxk 自己的进程模型问题，改自己的代码比接一个海外插件快得多。

**真要动的话，建议按这个顺序**：先把 `browser.cjs auto` 修好并跑通（一条命令填完一张表），再补文件字段的 accept 白名单抽取。前一件能把填表时间压到原来的三分之一，后一件直接减少你上传时的手动判断。
