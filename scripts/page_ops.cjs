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
      c.querySelectorAll('input,textarea,select').forEach(x => x.remove());
      const t = (c.innerText || '').replace(/\s+/g, ' ').trim();
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
    const total = p.querySelectorAll('input,textarea,select').length +
                  p.querySelectorAll(CUSTOM_SEL).length;
    const self = el.querySelectorAll('input,textarea,select').length +
                 el.querySelectorAll(CUSTOM_SEL).length + 1;
    if (total <= self) {
      const c = p.cloneNode(true);
      c.querySelectorAll('input,textarea,select,' + CUSTOM_SEL).forEach(x => x.remove());
      const t = (c.innerText || '').replace(/\s+/g, ' ').trim();
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

/** 按标签找控件（input / textarea / select 一起找） */
function findControl(label) {
  return [...document.querySelectorAll('input,textarea,select')].filter(isVisible).find(el => {
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
      readonly: el.readOnly ? 'Y' : '',
    };

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
    rows.push({
      i: rows.length,
      type: 'custom-select',
      label: labelAround(box) || (el.getAttribute('aria-label') || ''),
      value: (box.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 40),
      note: '自定义下拉：选项在浮层里，需要用户点',
    });
  });

  return { url: location.href, title: document.title, rows };
}

/**
 * 批量填。items: [{"label":"姓名","value":"张三"}]
 * 原生 select 会尝试代填并回读；自定义下拉一律回「需要用户点」，不尝试。
 */
function fillInPage(items) {
  const out = [];

  for (let i = 0; i < items.length; i++) {
    const it = items[i];
    const el = findControl(it.label);

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

// ---------- 拼成能在页面里独立执行的表达式 ----------

const CUSTOM_SEL_STR = '[role="combobox"],[aria-haspopup="listbox"],.ant-select,.el-select,' +
  '.el-cascader,.ivu-select,.beisen-select,[class*="select-trigger"]';
const CUSTOM_BOX_STR = '.ant-select,.el-select,.el-cascader,.ivu-select,.beisen-select';

const HELPERS = [isVisible, labelOf, labelAround, norm, pickOption, findControl, findCustomSelect,
  setInputValue, selectValue].map(f => f.toString()).join('\n');

const CONSTS = 'const CUSTOM_SEL=' + JSON.stringify(CUSTOM_SEL_STR) + ';' +
  'const CUSTOM_BOX=' + JSON.stringify(CUSTOM_BOX_STR) + ';';

/** 给 page.evaluate 用的自包含表达式 */
function dumpExpression() {
  return '(function(){' + HELPERS + CONSTS + 'return (' + dumpInPage.toString() + ')();})()';
}

function fillExpression(items) {
  return '(function(){' + HELPERS + CONSTS +
    'var items=' + JSON.stringify(items) + ';return (' + fillInPage.toString() + ')(items);})()';
}

module.exports = {
  dumpExpression, fillExpression,
  // 留出可单独调用的版本，方便别处复用或单测
  dumpInPage, fillInPage, pickOption, norm, labelOf, isVisible,
};
