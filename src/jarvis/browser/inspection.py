"""Fixed, read-only DOM inspection. No page/model-provided executable code."""

INSPECT = r"""() => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const name = el => norm(el.getAttribute('aria-label') ||
    [...(el.labels || [])].map(x => x.textContent).join(' ') ||
    (el.matches('button,a') ? el.innerText : el.type === 'submit' ? el.value : ''));
  const request = el => {
    if (el.matches('a[href]')) {
      if ((el.target && el.target !== '_self') || el.hasAttribute('download') ||
          el.hasAttribute('ping')) return null;
      return {url: el.href, method: 'GET', body: ''};
    }
    const form = el.form;
    if (!form || el.type !== 'submit' || (form.target && form.target !== '_self')) return null;
    const overrides = ['formaction','formmethod','formenctype','formtarget'];
    if (overrides.some(x => el.hasAttribute(x))) return null;
    if (form.enctype !== 'application/x-www-form-urlencoded' ||
        !['get','post'].includes(form.method)) return null;
    const fields = [];
    for (const f of form.elements) {
      if (f.matches(':disabled') || f.matches('button,input[type=submit]')) continue;
      if (!f.matches('textarea,input[type=text],input:not([type])') || !f.name ||
          f.hasAttribute('dirname')) return null;
      if (f.value.length > 4000 || fields.length >= 20) return null;
      fields.push([f.name, f.value.replace(/\r\n|\r|\n/g, '\r\n')]);
    }
    if (el.name) fields.push([el.name, el.value]);
    const body = new URLSearchParams(fields).toString();
    const url = new URL(form.action);
    if (form.method === 'get') url.search = body;
    return {url: url.href, method: form.method.toUpperCase(),
      body: form.method === 'post' ? body : '',
      fields: fields.map(([name,value]) => ({name,value}))};
  };
  const selector = 'a[href],button,input[type=submit],input[type=text],input:not([type]),textarea';
  const all = [...document.querySelectorAll(selector)];
  const elements = all.filter(el => el.getClientRects().length && !el.matches(':disabled') &&
      getComputedStyle(el).visibility !== 'hidden' && !el.readOnly)
    .map(el => ({role: el.matches('a') ? 'link' :
        el.matches('button,input[type=submit]') ? 'button' : 'textbox',
      name: name(el), html: el.outerHTML,
      value: el.matches('textarea,input[type=text],input:not([type])') ? el.value : '',
      request: request(el)}))
    .filter(el => el.name && el.name.length <= 300 &&
        el.html.length <= 100000 && el.value.length <= 4000)
    .slice(0, 50);
  const states = [...document.querySelectorAll('input,textarea,select')]
    .map(el => [el.value,el.checked,el.selectedIndex]);
  const material = document.documentElement.outerHTML + JSON.stringify(states);
  return {material: material.length <= 2000000 ? material : '', title: document.title.slice(0,500),
    text: (document.body?.innerText || '').slice(0,12000), elements};
}"""
