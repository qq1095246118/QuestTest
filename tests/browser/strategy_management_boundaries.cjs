// Real deployed components with browser-only fixtures. These are NOT API/source passes.
const fs = require('fs');
const path = require('path');
const fields = ['margin_equity', 'unrealized_pnl', 'realized_pnl_gross',
  'cumulative_return', 'annualized_return', 'current_drawdown', 'commission_expense',
  'funding_pnl', 'position_utilization', 'sharpe_ratio', 'sortino_ratio', 'calmar_ratio',
  'max_drawdown', 'volatility', 'win_rate', 'profit_loss_ratio'];
const variants = [
  ['positive', '123.456789'], ['negative', '-0.123456789'], ['zero', '0'],
  ['large', '1234567890123.123456'], ['tiny', '0.00000089'],
  ['unavailable', null], ['absent', undefined],
];
module.exports = async ({page, context, network, snapshot, out}) => {
  const result = {mode: 'UI_ONLY_RESPONSE_FIXTURE', backend_calculation_verified: false,
    checks: [], scenarios: [], gaps: [], at: new Date().toISOString()};
  const record = (name, ok, detail) => result.checks.push({name, status: ok ? 'PASS' : 'FAIL', detail});
  const save = () => fs.writeFileSync(path.join(out, 'display-boundary-results.json'), JSON.stringify(result, null, 2));
  const id = 'strat_d2698adb3b8d4f9b9056aeb958712b6c';
  const url = 'https://test-factor-frontend.questvector.ai/quant-trading/strategies/' + id + '?exchange=binance';
  const baseline = network.find(r => r.status === 200 && r.name.includes('/analytics/summary?') && new URL(r.name).searchParams.get('subject_id') === id && fields.every(f => r.body?.data?.metrics?.[f]))?.body;
  if (!baseline) throw new Error('No actual summary schema baseline');
  for (const [name, value] of variants) {
    const body = structuredClone(baseline);
    for (const field of fields) {
      if (value === undefined) delete body.data.metrics[field];
      else Object.assign(body.data.metrics[field], {value, status: value === null ? 'unavailable' : 'complete', reason_code: value === null ? 'SOURCE_FIELD_UNAVAILABLE' : null});
    }
    const pattern = '**/quantitative-trading/analytics/summary?**';
    const handler = async route => {
      if (new URL(route.request().url()).searchParams.get('subject_id') !== id) return route.fallback();
      await route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(body)});
    };
    await context.route(pattern, handler);
    try {
      await page.goto(url, {waitUntil: 'domcontentloaded', timeout: 30000});
      await page.locator('.q2-info-tab').first().click();
      await page.waitForTimeout(3500);
      const s = await snapshot('boundary-' + name);
      const cards = await page.locator('.q2-metric-card').allInnerTexts();
      record(name + '/card-count', cards.length === fields.length, {count: cards.length});
      cards.forEach((card, i) => {
        const display = card.split('\n')[1] || '', missing = ['-', '--', '不可用'].includes(display);
        const numeric = Number(display.replace(/[,＋+%]/g, ''));
        let ok;
        if (value == null) ok = missing;
        else if (display.includes('%')) {
          const expected = Math.trunc(Number(value) * 10000) / 100;
          ok = Number.isFinite(numeric) && Math.abs(numeric - expected) <= Math.max(0.000001, Math.abs(expected) * 1e-15);
        } else if (name === 'tiny') {
          // User accepts existing tiny-number formatting; do not impose a new rule.
          ok = !missing && !/NaN|Infinity|undefined/.test(display);
        } else {
          // Monetary two-vs-six-place formatting is explicitly outside this bug sweep.
          const expected = Math.trunc(Number(value) * 100) / 100;
          ok = Number.isFinite(numeric) && Math.abs(numeric - expected) <= Math.max(0.000001, Math.abs(expected) * 1e-15);
        }
        record(name + '/' + fields[i], ok, {input: value ?? null, omitted: value === undefined, display});
      });
      record(name + '/no-document-overflow', s.document.width <= s.viewport.width + 1, s.document);
      record(name + '/no-render-exception', !/数据加载失败|NaN|Infinity|undefined/.test(s.text), {screenshot: 'boundary-' + name + '.png'});
      result.scenarios.push({name, fields: [...fields], screenshots: ['boundary-' + name + '.png']});
    } finally { await context.unroute(pattern, handler); save(); }
  }
  const catalog = [...network].reverse().find(r => {
    if (r.status !== 200 || !r.name.includes('/quantitative-trading/strategies?') || r.body?.data?.items?.length < 2) return false;
    const query = new URL(r.name).searchParams;
    return !query.get('search') && !query.get('runtime_status') && !query.get('researcher_user_id');
  })?.body;
  if (!catalog) result.gaps.push('Missing actual catalog response for long-name tests');
  else {
    const body = structuredClone(catalog), texts = ['策略多账户收益监控'.repeat(30), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'.repeat(10)];
    body.data.items.slice(0, 2).forEach((row, i) => {row.name = texts[i]; row.strategy_name = texts[i];});
    const pattern = '**/quantitative-trading/strategies?**';
    const handler = route => route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(body)});
    await context.route(pattern, handler);
    try {
      await page.goto('https://test-factor-frontend.questvector.ai/quant-trading/strategies', {waitUntil: 'domcontentloaded', timeout: 30000});
      await page.locator('table tbody tr').first().waitFor({state: 'visible', timeout: 30000});
      for (const width of [1280, 1920, 2560]) {
        await page.setViewportSize({width, height: 1080});
        const s = await snapshot('boundary-long-names-' + width);
        record('long-names/' + width + '/full-name-retained', texts.every(t => s.text.includes(t)), {});
        record('long-names/' + width + '/document-contained', s.document.width <= width + 1, s.document);
        record('long-names/' + width + '/adjacent-amounts-preserved', s.tables[0]?.rows.slice(0, 2).every(r => r[5] && r[16]?.includes('详情')), {});
      }
      result.scenarios.push({name: 'long_names', viewports: [1280, 1920, 2560]});
    } finally { await context.unroute(pattern, handler); await page.setViewportSize({width: 1920, height: 1080}); save(); }
  }
  result.summary = {scenarios: result.scenarios.length, field_variant_pairs: variants.length * fields.length,
    assertions: result.checks.length, passed: result.checks.filter(x => x.status === 'PASS').length,
    failed: result.checks.filter(x => x.status === 'FAIL').length};
  save();
  return {summary: result.summary, failed: result.checks.filter(x => x.status === 'FAIL'), gaps: result.gaps};
};
