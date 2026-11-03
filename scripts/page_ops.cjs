/**
 * page_ops.cjs —— 在网页里执行的「看字段 / 填字段」逻辑
 *
 * 为什么单独一个文件：browser.cjs 只是把这里的表达式塞进 page.evaluate，
 * 测试脚本用 Playwright 起个本地页面跑同一份代码，不用连真浏览器。
 *
 * 为什么要「拼字符串」：page.evaluate 会把函数 toString 之后丢到页面里执行，
 * 函数里引用的模块作用域变量（其他辅助函数、常量）在页面里是不存在的。
 * 所以这里把辅助函数一起拼进去，生成一段自包含的表达式。
 */
function isVisible(el) {
  const r = el.getBoundingClientRect();
  return r.width > 2 && r.height > 2;
}

/** 字段名：向上找最近的、只包住这一个控件的容器，拿它的文字
 *
 * 关键：容器里 select 的 innerText 会带上全部选项文字，直接取会把标签淹没，
 * 所以先把容器里所有表单控件抠掉再取文字。
 */
function labelOf(el) {
  let p = el.parentElement, d = 0;
  while (p && d < 6) {
    const n = p.querySelectorAll('input,textarea,select').length;
    if (n === 1) {
      const c = p.cloneNode(true);
      c.querySelectorAll(STRIP_SEL).forEach(x => x.remove());
      // 只取第一行：容器的第二行往后常常是提示语（「无准确的毕业时间可填写预计毕业时间」）
      const t = (c.innerText || '').split('\n').map(s => s.trim()).filter(Boolean)[0] || '';
      if (t && t.length < 40) return t.slice(0, 30);
    }
    if (n > 1) break;
    p = p.parentElement; d++;
  }
  return '';
}

/** 自定义下拉的字段名：它的容器里可能一个原生控件都没有（纯 div 触发器），
 *  所以不能沿用 labelOf 的「只包住一个控件」规则，改成「除了它自己，没有别的控件」。 */
function labelAround(el) {
  let p = el.parentElement, d = 0;
  while (p && d < 6) {
    const total = p.querySelectorAll(WIDGET_SEL).length;
    const self = el.querySelectorAll(WIDGET_SEL).length + 1;
    if (total <= self) {
      const c = p.cloneNode(true);
      c.querySelectorAll(STRIP_SEL).forEach(x => x.remove());
      const t = (c.innerText || '').split('\n').map(s => s.trim()).filter(Boolean)[0] || '';
      if (t && t.length < 40) return t.slice(0, 30);
    }
    if (total > self) break;
    p = p.parentElement; d++;
  }
  return '';
}

/** 比对用的归一化：全角半角统一、去掉所有空白 */
function norm(s) {
  return (s == null ? '' : String(s)).normalize('NFKC').replace(/\s+/g, '');
}

/** 这个字段是不是必填
 *
 * 三种标法都要认：
 *   1. required / aria-required 属性（原生表单）
 *   2. 容器文字里有星号（很多后台系统）
 *   3. label 或祖先节点带 required 类（飞书招聘：<label class="atsx-form-item-required">，
 *      星号是 CSS 画的，innerText 里根本没有——只认前两种会漏掉整张表的必填项）
 */
function requiredMark(el) {
  if (el.required === true) return 'Y';
  if ((el.getAttribute('aria-required') || '') === 'true') return 'Y';

  // 自定义下拉的外层容器本身没有 id，真正带 id 的是里面那个输入框
  const probe = (el.id ? el : el.querySelector('input,textarea,select'));
  if (probe && probe.id) {
    const sel = window.CSS && CSS.escape ? CSS.escape(probe.id) : probe.id;
    const lab = document.querySelector('label[for="' + sel + '"]');
    if (lab && /required/i.test(lab.className || '')) return 'Y';
  }

  // 同一个表单项里的 label：从最近的 form-item 往上找三层，
  // 因为 label 和控件常常是两个兄弟节点，不在一层里
  let node = el.closest('[class*="form-item"]');
  for (let i = 0; node && i < 3; i++) {
    const lab = node.querySelector('label');
    if (lab && /required/i.test(lab.className || '')) return 'Y';
    const up = node.parentElement;
    node = up && up.closest ? up.closest('[class*="form-item"]') : null;
  }

  let p = el.parentElement, d = 0;
  while (p && d < 6) {
    const cls = (p.className || '').toString();
    if (/(^|[-_])required([-_]|$)|form-item-required/i.test(cls)) return 'Y';
    const t = (p.innerText || '');
    if (t.length < 60 && t.indexOf('*') >= 0) return 'Y';
    if (p.querySelectorAll('input,textarea,select').length > 1) break;
    p = p.parentElement; d++;
  }
  return '';
}

/**
 * 在原生 select 里挑一个选项
 * 完全同名唯一 → 用；包含关系唯一 → 用；多个都像 → 报歧义，不猜；一个都不像 → 报没找到。
 */
function pickOption(el, want) {
  const w = norm(want);
  const opts = [...el.options].filter(o => !o.disabled);
  const all = opts.map(o => (o.text || '').trim());

  const exact = opts.filter(o => norm(o.text) === w || norm(o.value) === w);
  if (exact.length === 1) return { opt: exact[0], how: 'exact' };
  if (exact.length > 1) {
    return { err: '多个选项完全同名，没法判断该选哪个', candidates: exact.map(o => o.text.trim()) };
  }

  const partial = opts.filter(o => {
    const t = norm(o.text);
    return !!t && (t.indexOf(w) >= 0 || w.indexOf(t) >= 0);
  });
  if (partial.length === 1) return { opt: partial[0], how: 'partial' };
  if (partial.length > 1) {
    return { err: '有多个选项都像，不猜', candidates: partial.map(o => o.text.trim()) };
  }
  return { err: '选项里没有这一项', candidates: all.slice(0, 20) };
}

/** 按标签找控件，返回全部命中的（同名标签在表单里很常见，比如两个「获奖时间」） */
function findControls(label) {
  return [...document.querySelectorAll('input,textarea,select')].filter(isVisible).filter(el => {
    if ((el.getAttribute('type') || '').toLowerCase() === 'file') return false;
    let p = el.parentElement, d = 0;
    while (p && d < 6) {
      const n = p.querySelectorAll('input,textarea,select').length;
      const t = (p.innerText || '').replace(/\s+/g, ' ').trim();
      if (n === 1 && t && t.indexOf(label) >= 0) return true;
      if (n > 1) break;
      p = p.parentElement; d++;
    }
    return false;
  });
}

/** 按标签找自定义下拉的容器（只为告诉调用方「这里需要用户点」） */
function findCustomSelect(label) {
  return [...document.querySelectorAll(CUSTOM_SEL)].find(el => {
    const box = el.closest(CUSTOM_BOX) || el;
    if (!isVisible(box)) return false;
    const t = labelAround(box) || el.getAttribute('aria-label') || '';
    return t.indexOf(label) >= 0;
  });
}

/** 给原生控件赋值：走原生 setter + 真实事件（React 受控组件只认这个） */
function setInputValue(el, v) {
  const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement : HTMLInputElement;
  const setter = Object.getOwnPropertyDescriptor(proto.prototype, 'value').set;
  setter.call(el, v);
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  el.dispatchEvent(new Event('blur', { bubbles: true }));
}

/** 给原生 select 选中某一项，并回读确认真的选中了 */
function selectValue(el, opt) {
  const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set;
  setter.call(el, opt.value);
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  el.dispatchEvent(new Event('blur', { bubbles: true }));
  const now = el.selectedOptions && el.selectedOptions[0];
  return now ? (now.text || '').trim() : '';
}

/**
 * 导出当前页所有字段
 * 原生 select 会带 options 与当前选中文字；自定义下拉标成 custom-select，只读显示值。
 */
function dumpInPage() {
  const rows = [];

  document.querySelectorAll('input,textarea,select').forEach((el, i) => {
    if (!isVisible(el)) return;
    const type = (el.getAttribute('type') || el.tagName).toLowerCase();
    if (type === 'hidden' || type === 'file') return;

    const row = {
      i,
      type: el.tagName === 'SELECT' ? 'select' : type,
      label: labelOf(el),
      placeholder: el.getAttribute('placeholder') || '',
      value: (el.value || '').toString().slice(0, 40),
      name: el.name || '',
      checked: /^(radio|checkbox)$/.test(type) ? !!el.checked : undefined,
      readonly: el.readOnly ? 'Y' : '',
      required: requiredMark(el),
    };
    row.empty = !row.value;

    if (el.tagName === 'SELECT') {
      // 读选项：这是能不能安全代填的前提——不知道有哪些选项就只能猜
      row.options = [...el.options].map(o => ({
        text: (o.text || '').trim(),
        value: o.value,
        disabled: o.disabled ? 'Y' : '',
      }));
      const sel = el.selectedOptions && el.selectedOptions[0];
      row.value = sel ? (sel.text || '').trim() : '';
      row.multiple = el.multiple ? 'Y' : '';
    }
    rows.push(row);
  });

  // 自定义下拉：不是 select，是 div 触发器加一层浮层，原生遍历看不见
  const seen = [];
  document.querySelectorAll(CUSTOM_SEL).forEach(el => {
    const box = el.closest(CUSTOM_BOX) || el;
    if (seen.indexOf(box) >= 0) return;
    if (!isVisible(box)) return;
    seen.push(box);
    // 值在里层 input 的 value 上（element-ui、北森都是 readonly input 显示已选文字），
    // 而 innerText 往往只有字段名。只看 innerText 会把「已经选好」的判成空——
    // 2026-11-03 在测试夹具上就这么误报了三个下拉，体检说必填还空着，其实早填上了。
    const inp = box.querySelector('input');
    const fallback = (box.innerText || '').replace(/\s+/g, ' ').trim();
    const shown = (inp ? (inp.value || '') : fallback).trim().slice(0, 40);
    rows.push({
      i: rows.length,
      type: 'custom-select',
      label: labelAround(box) || (el.getAttribute('aria-label') || ''),
      value: shown,
      required: requiredMark(box),
      empty: !shown.replace(/\s+/g, '') || /^(请选择|请输入)/.test(shown),
      note: '自定义下拉：选项在浮层里，需要用户点',
    });
  });

  // 无文字的图标按钮（「添加」这类）。2026-11-03 两次栽在这上面：dump 看不见它们，
  // 我就断定「这一页没有添加入口」，其实娃哈哈的教育/工作/项目/简历全是
  // <img alt="addIcon">。这类元素不进 audit（它们不是字段），只作提示列出来。
  document.querySelectorAll('img[alt],button[aria-label],[role="button"][aria-label]').forEach(el => {
    const alt = (el.getAttribute('alt') || el.getAttribute('aria-label') || '').trim();
    if (!alt || !isVisible(el)) return;
    const box = el.closest('.EditorCard,.card,section,.el-form-item,.el-card') || el.parentElement;
    rows.push({
      i: rows.length,
      type: 'icon-button',
      label: alt,
      value: '',
      context: box ? (box.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 30) : '',
      empty: false,
      note: '无文字按钮：点它可能展开被折叠的区块或新增一行',
    });
  });

  // 日期区间控件：值在一个 0×0 的隐藏 input 里，真正的值显示在外层容器文字上。
  // 不单独报的话，dump 会让人以为日期是空的——实测飞书招聘就是这样。
  const seenDate = [];
  document.querySelectorAll('[class*="date-picker"]').forEach(el => {
    // 只认最外层的容器：里面的隐藏 input、内层节点都会命中这个选择器
    if (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)) return;
    if (el.closest('[class*="date-picker"]') !== el) return;
    if (!isVisible(el)) return;
    if (el.querySelector('input:not([class*="hidden"]),textarea,select')) return;  // 里面还有真输入框，按控件算
    const key = labelAround(el);
    if (!key) return;
    // 不按字段名去重：两个「起止时间」是两个真实控件，都得报出来
    seenDate.push(el);
    rows.push({
      i: rows.length,
      type: 'date-range',
      label: key,
      value: (el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 40),
      required: requiredMark(el),
      empty: !(el.innerText || '').replace(/\s+/g, '').trim(),
      note: '日期区间：值是显示文字，改要用户点',
    });
  });

  // 体检：空着的字段分必填/非必填两类。
  // 非必填且信息库里没有的，不代填，只报给用户让他自己决定。
  const skip = { hidden: 1, file: 1 };
  const reqEmpty = [], optEmpty = [];
  rows.forEach(r => {
    if (skip[r.type] || !r.empty) return;
    if (r.type === 'radio' || r.type === 'checkbox') return; // audited as a group below
    (r.required === 'Y' ? reqEmpty : optEmpty).push(r.label || ('未命名字段#' + r.i));
  });

  const choices = new Map();
  rows.filter(r => r.type === 'radio' || r.type === 'checkbox').forEach(r => {
    const key = r.type + ':' + (r.name || r.label || ('#' + r.i));
    if (!choices.has(key)) choices.set(key, { label: r.label || ('未命名字段#' + r.i),
      required: false, checked: false });
    const group = choices.get(key);
    group.required = group.required || r.required === 'Y';
    group.checked = group.checked || r.checked === true;
  });
  choices.forEach(group => {
    if (group.checked) return;
    (group.required ? reqEmpty : optEmpty).push(group.label);
  });

  return {
    url: location.href, title: document.title, rows,
    // 有没有「打开着、已经填了值、但还没点保存」的编辑面板——切区块前先看这个
    unsaved_panel: panelScanInPage(),
    audit: {
      required_empty: reqEmpty,
      optional_empty: optEmpty,
      required_empty_count: reqEmpty.length,
      optional_empty_count: optEmpty.length,
    },
  };
}

/**
 * 批量填。items: [{"label":"姓名","value":"张三"}]
 * 原生 select 会尝试代填并回读；自定义下拉一律回「需要用户点」，不尝试。
 */
function fillInPage(items) {
  const out = [];

  for (let i = 0; i < items.length; i++) {
    const it = items[i];
    // 同名标签（比如两个「获奖时间」）用 nth 指定第几个，从 0 开始
    const list = findControls(it.label);
    const el = list[it.nth || 0];

    if (!el) {
      const custom = findCustomSelect(it.label);
      if (custom) {
        const box = custom.closest(CUSTOM_BOX) || custom;
        out.push({
          label: it.label, ok: false, kind: 'custom-select',
          why: '自定义下拉，需要用户点', got: (box.innerText || '').trim().slice(0, 30),
        });
      } else {
        out.push({ label: it.label, ok: false, kind: 'missing', why: '没找到该字段' });
      }
      continue;
    }

    if (el.tagName === 'SELECT') {
      if (el.multiple) {
        out.push({ label: it.label, ok: false, kind: 'select', why: '多选框不代填，需要用户选' });
        continue;
      }
      const picked = pickOption(el, it.value);
      if (picked.err) {
        out.push({
          label: it.label, ok: false, kind: 'select',
          why: picked.err, candidates: picked.candidates,
        });
        continue;
      }
      const got = selectValue(el, picked.opt);
      out.push({
        label: it.label, ok: got === (picked.opt.text || '').trim(), kind: 'select',
        got: got, matched: (picked.opt.text || '').trim(), how: picked.how,
      });
      continue;
    }

    setInputValue(el, it.value);
    out.push({
      label: it.label, ok: el.value === it.value, kind: 'input',
      got: (el.value || '').toString().slice(0, 30),
    });
  }

  return out;
}

/** 按标签找自定义下拉的容器（可点击打开浮层的那个外层节点） */
function widgetBoxInPage(label) {
  const boxes = [];
  document.querySelectorAll(CUSTOM_SEL).forEach(el => {
    const box = el.closest(CUSTOM_BOX) || el;
    if (boxes.indexOf(box) < 0) boxes.push(box);
  });
  for (let i = 0; i < boxes.length; i++) {
    const b = boxes[i];
    if (!isVisible(b)) continue;
    const t = labelAround(b) || b.getAttribute('aria-label') || '';
    if (t.indexOf(label) >= 0) return b;
  }
  return null;
}

/** 读回某个下拉现在显示的值（点完之后核对用） */
function boxTextInPage(label) {
  const b = widgetBoxInPage(label);
  if (!b) return null;
  const inp = b.querySelector('input');
  return {
    text: (b.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 40),
    value: inp ? (inp.value || '').slice(0, 40) : '',
  };
}

function widgetExpression(label) {
  return '(function(){' + HELPERS + CONSTS + 'return (' + widgetBoxInPage.toString() +
    ')(' + JSON.stringify(label) + ');})()';
}

function boxTextExpression(label) {
  return '(function(){' + HELPERS + CONSTS + 'return (' + boxTextInPage.toString() +
    ')(' + JSON.stringify(label) + ');})()';
}

/** 扫「打开着的编辑面板」：页面上有没有正在编辑、且已经填了值的一区块。
 *  为什么需要：娃哈哈那种分节编辑器是内存态，不点保存就切到下一块，前一块整个消失。
 *  2026-11-03 就这样丢了 13 项。切之前先问一句这个，比事后重填便宜得多。
 *
 *  做法是**从「保存按钮」往上找**最近的、带输入框的容器，而不是从页面往下扫——
 *  往下扫会把整个页面当成一个面板（洽洽实测报「首页，已填 31 项」，天天报警等于没报）。
 *  输入框超过 20 个的按整页表单处理，不算「一块」，只在 note 里提一句。 */
function panelScanInPage() {
  const SAVE = /^(提交|保存|确定|暂存)$/;
  const panels = [];
  const seenEl = [];
  [...document.querySelectorAll('button,span,a,div')].forEach(btn => {
    if (btn.children.length || !isVisible(btn) || !SAVE.test((btn.innerText || '').trim())) return;
    let box = btn.parentElement, depth = 0;
    while (box && depth < 6) {
      const inputs = [...box.querySelectorAll('input,textarea')].filter(isVisible);
      if (inputs.length) break;
      box = box.parentElement; depth++;
    }
    if (!box || seenEl.indexOf(box) >= 0) return;
    seenEl.push(box);
    const inputs = [...box.querySelectorAll('input,textarea')].filter(isVisible);
    const filled = inputs.filter(el => (el.value || '').trim()).length;
    if (!filled) return;
    const title = ((box.innerText || '').split('\n')[0] || '').trim().slice(0, 20);
    panels.push({
      title: title, filled: filled, total: inputs.length,
      button: (btn.innerText || '').trim(),
      scope: inputs.length > 20 ? 'page' : 'block',
    });
  });
  // 只报「一块」，整页表单不算；整页的单独放在 page_scope 里让人知道，但不拦
  const blocks = panels.filter(p => p.scope === 'block').sort((a, b) => b.filled - a.filled);
  const pages = panels.filter(p => p.scope === 'page');
  if (blocks.length) return Object.assign({ found: true }, blocks[0]);
  if (pages.length) return { found: false, page_scope: pages[0] };
  return { found: false };
}

// ---------- 拼成能在页面里独立执行的表达式 ----------

const CUSTOM_SEL_STR = '[role="combobox"],[aria-haspopup="listbox"],.ant-select,.el-select,' +
  '.el-cascader,.ivu-select,.beisen-select,.phoenix-select,[class*="atsx-select"],' +
  '[class*="select-trigger"]';
const CUSTOM_BOX_STR = '.ant-select,.el-select,.el-cascader,.ivu-select,.beisen-select,' +
  '.phoenix-select,[class*="atsx-select"]';
// 取字段名时要先抠掉的东西：控件本体、选择器容器、日期区间容器
const WIDGET_SEL_STR = 'input,textarea,select,' + CUSTOM_SEL_STR + ',[class*="date-picker"]';
const STRIP_SEL_STR = WIDGET_SEL_STR;

const HELPERS = [isVisible, labelOf, labelAround, norm, requiredMark, pickOption, findControls,
  findCustomSelect, setInputValue, selectValue, widgetBoxInPage, panelScanInPage]
  .map(f => f.toString()).join('\n');

const CONSTS = 'const CUSTOM_SEL=' + JSON.stringify(CUSTOM_SEL_STR) + ';' +
  'const CUSTOM_BOX=' + JSON.stringify(CUSTOM_BOX_STR) + ';' +
  'const WIDGET_SEL=' + JSON.stringify(WIDGET_SEL_STR) + ';' +
  'const STRIP_SEL=' + JSON.stringify(STRIP_SEL_STR) + ';';

/** 给 page.evaluate 用的自包含表达式 */
function dumpExpression() {
  return '(function(){' + HELPERS + CONSTS + 'return (' + dumpInPage.toString() + ')();})()';
}

function fillExpression(items) {
  return '(function(){' + HELPERS + CONSTS +
    'var items=' + JSON.stringify(items) + ';return (' + fillInPage.toString() + ')(items);})()';
}

function panelExpression() {
  return '(function(){' + HELPERS + CONSTS + 'return (' + panelScanInPage.toString() + ')();})()';
}

module.exports = {
  dumpExpression, fillExpression, widgetExpression, boxTextExpression, panelExpression,
  // 留出可单独调用的版本，方便别处复用或单测
  dumpInPage, fillInPage, pickOption, norm, labelOf, isVisible, panelScanInPage,
};
