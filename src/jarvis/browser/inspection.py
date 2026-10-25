"""Fixed, read-only DOM inspection. No page/model-provided executable code."""

INSPECT = r"""(live = false) => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const name = el => norm(el.getAttribute('aria-label') ||
    (live ? (el.getAttribute('aria-labelledby') || '').split(/\s+/)
      .map(id => document.getElementById(id)?.textContent || '').join(' ') : '') ||
    [...(el.labels || [])].map(x => x.textContent).join(' ') ||
    (el.matches('button,a,[role=button]') ? el.innerText : el.type === 'submit' ? el.value : '') ||
    (live ? el.getAttribute('placeholder') : ''));
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
  const editable = live ? 'textarea,input:not([type]),input[type=text],input[type=search],' +
    'input[type=email],input[type=url],input[type=tel],[contenteditable=true]' :
    'textarea,input[type=text],input:not([type])';
  const selector = 'a[href],button,input[type=submit],' + editable + (live ? ',[role=button]' : '');
  const all = [...document.querySelectorAll(selector)];
  const elements = all.filter(el => el.getClientRects().length && !el.matches(':disabled') &&
      getComputedStyle(el).visibility !== 'hidden' && !el.readOnly)
    .map(el => ({role: el.matches('a') ? 'link' :
        el.matches('button,input[type=submit],[role=button]') ? 'button' :
        el.matches('input[type=search]') ? 'searchbox' : 'textbox',
      name: name(el), html: el.outerHTML,
      value: el.matches(editable) ? (el.value ?? el.innerText ?? '') : '',
      request: request(el)}))
    .filter(el => el.name && el.name.length <= 300 &&
        el.html.length <= 100000 && el.value.length <= 4000)
    .slice(0, 50);
  const states = [...document.querySelectorAll('input,textarea,select')]
    .map(el => [el.value,el.checked,el.selectedIndex]);
  const material = live ? JSON.stringify([document.title,
    (document.body?.innerText || '').slice(0,12000), elements, states]) :
    document.documentElement.outerHTML + JSON.stringify(states);
  return {material: material.length <= 2000000 ? material : '', title: document.title.slice(0,500),
    text: (document.body?.innerText || '').slice(0,12000), elements,
    has_unsaved_changes: [...document.querySelectorAll('input,textarea')]
      .some(el => el.value !== el.defaultValue)};
}"""
