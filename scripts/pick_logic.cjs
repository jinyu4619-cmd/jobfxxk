/**
 * pick_logic.cjs —— 自定义下拉的代填逻辑（点开 → 读选项 → 点中 → 回读）
 *
 * 单独抽出来是为了能测：browser.cjs 和测试脚本用的是同一份代码，
 * 不然测试通过只说明测试写对了，说明不了产品能用。
 *
 * 四条硬规则，缺一条就停，绝不猜：
 *   1. 选项列表必须是虚的（没选项就报「点开了但没看到选项」）
 *   2. 唯一命中才点：先完全同名，再唯一包含；多个都像 → 报候选，不选
 *   3. 点完立刻回读触发器上的显示文字，对不上算失败
 *   4. 不滚动找、不重试
 */

const OPTION_SELECTOR = '[role="option"], .atsx-select-dropdown li, .ant-select-item-option, ' +
  '.el-select-dropdown__item, .ivu-select-item, li[class*="option"], ' +
  // 北森 zhiye.com 的 phoenix 组件：选项是 ul.phoenix-selectList__list 下的 li，
  // 没有 role，也没有 option 字样。2026-11-03 实测「政治面貌」点开后一个都匹配不到。
  '.phoenix-selectList__listItem';

const normText = s => String(s == null ? '' : s).replace(/\s+/g, '');

function choose(options, want) {
  const w = normText(want);
  if (!w) return { err: '没给要选的值' };

  const exact = options.filter(o => normText(o.text) === w);
  if (exact.length === 1) return { hit: exact[0], how: 'exact' };
  if (exact.length > 1) {
    // 文字完全一样，选哪个在界面上没有区别；取第一个并标出来，别让用户白点一次
    return { hit: exact[0], how: 'exact-dup', dupCount: exact.length };
  }

  const part = options.filter(o => {
    const t = normText(o.text);
    return t && (t.indexOf(w) >= 0 || w.indexOf(t) >= 0);
  });
  if (part.length === 1) return { hit: part[0], how: 'partial' };
  if (part.length > 1) {
    // 部分匹配到多个、但文字一模一样时（同程的「学历类型」就是三个「硕士」），
    // 选哪个在界面上看不出区别，取第一个并标注候选数；文字不同的仍然拒绝。
    const first = normText(part[0].text);
    if (part.every(o => normText(o.text) === first)) {
      return { hit: part[0], how: 'partial-dup', dupCount: part.length };
    }
    return { err: '多个选项都像，不猜', candidates: part };
  }
  return { err: '选项里没有这一项', candidates: options };
}

/** 收起浮层。北森的 phoenix 不响应 Esc（2026-11-03 实测），
 *  用户教的：点最左或最右的空白处一下就能退出——所以先找一个真正的空白点再点，
 *  找不到才退回 Esc。找空白点是为了不误点到侧边导航或链接。 */
async function closePopup(page) {
  const pt = await page.evaluate(() => {
    const w = innerWidth, h = innerHeight;
    const cands = [[2, Math.round(h / 2)], [w - 3, Math.round(h / 2)],
                   [Math.round(w / 2), h - 4], [2, 4]];
    for (const c of cands) {
      const el = document.elementFromPoint(c[0], c[1]);
      if (!el) continue;
      if (/^(HTML|BODY)$/.test(el.tagName)) return c;
      if (!el.children.length && !(el.textContent || '').trim() && !el.onclick) return c;
    }
    return null;
  }).catch(() => null);
  if (pt) await page.mouse.click(pt[0], pt[1]).catch(() => {});
  await page.waitForTimeout(150);
  await page.keyboard.press('Escape').catch(() => {});
}

async function pickOnPage(page, ops, items, { waitMs = 120, tries = 10 } = {}) {
  const out = [];

  for (const it of items) {
    const handle = await page.evaluateHandle(ops.widgetExpression(it.label));
    const el = handle.asElement();
    if (!el) {
      out.push({ label: it.label, ok: false, why: '没找到这个自定义下拉' });
      await handle.dispose();
      continue;
    }

    await el.click({ timeout: 5000 }).catch(() => {});
    // 数可见选项；浮层本来就开着时，上面这一下会把它关掉，所以「一个都没有」时
    // 再点一次开回来。只多这一次，且只在完全没选项时——不会重复去点选项本身。
    const pollOptions = async () => {
      let n = 0;
      for (let i = 0; i < tries; i++) {
        await page.waitForTimeout(waitMs);
        n = await page.locator(OPTION_SELECTOR + ':visible').count();
        if (n > 0) break;
      }
      return n;
    };
    let n = await pollOptions();
    if (!n) {
      await el.click({ timeout: 5000 }).catch(() => {});
      n = await pollOptions();
    }
    if (!n) {
      out.push({ label: it.label, ok: false, why: '点开了但没看到选项' });
      await closePopup(page);
      await handle.dispose();
      continue;
    }

    const opts = [];
    const loc = page.locator(OPTION_SELECTOR + ':visible');
    for (let i = 0; i < n; i++) {
      const t = (await loc.nth(i).innerText().catch(() => '')).trim();
      if (t) opts.push({ i: i, text: t });
    }

    const picked = choose(opts, it.value);
    if (picked.err) {
      out.push({
        label: it.label, ok: false, why: picked.err,
        candidates: (picked.candidates || []).map(o => o.text).slice(0, 10),
      });
      await closePopup(page);
      await handle.dispose();
      continue;
    }

    await loc.nth(picked.hit.i).click({ timeout: 5000 }).catch(() => {});
    await page.waitForTimeout(250);
    // 选完之后把浮层收干净，免得下一个字段点开时撞上还开着的那一层
    await closePopup(page);

    const back = await page.evaluate(ops.boxTextExpression(it.label)).catch(() => null);
    const got = back ? String(back.text || back.value || '').trim() : '';
    out.push({
      label: it.label, ok: !!got && normText(got).indexOf(normText(picked.hit.text)) >= 0,
      kind: 'custom-select', got: got, matched: picked.hit.text, how: picked.how,
      dup: picked.dupCount || undefined,
    });
    await handle.dispose();
  }

  return out;
}

module.exports = { pickOnPage, choose, OPTION_SELECTOR };
