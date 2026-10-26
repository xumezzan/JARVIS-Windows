"""Fixed DOM inspection and private-field filtering; no caller-supplied selectors/code."""

import json

STRICT_SELECTOR = "a[href],button,input[type=submit],textarea,input[type=text],input:not([type])"
LIVE_SELECTOR = (
    STRICT_SELECTOR + ",input[type=search],input[type=email],input[type=url],input[type=tel],"
    '[contenteditable="true"],[contenteditable="plaintext-only"],[role="textbox"],'
    '[role="button"],[role="tab"],[role="menuitem"],[role="combobox"],[role="option"]'
)

# Shared with the last actionability check. This reads metadata, never field values.
SAFE_ELEMENT = r"""el => {
  const blocked = '[type=password],[autocomplete=current-password],[autocomplete=new-password],'
    + '[autocomplete=one-time-code],[data-private],[aria-hidden=true]';
  const privateName = new RegExp(
    'password|passcode|one.?time.?code|secret|token|credential|парол|одноразов|токен','i');
  if (!el.isConnected || el.closest(blocked)) return false;
  for (let node = el; node; node = node.parentElement) {
    if (privateName.test(['name','id','aria-label','autocomplete']
        .map(a => node.getAttribute(a) || '').join(' '))) return false;
  }
  return true;
}"""

INSPECT = (
    r"""(live = false) => {
  const safe = __SAFE__;
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const selector = live ? __LIVE__ : __STRICT__;
  const editable = el => el.isContentEditable || el.matches(
    'textarea,input:not([type]),input[type=text],input[type=search],input[type=email],'
    + 'input[type=url],input[type=tel]');
  const safeClone = el => {
    const copy = el.cloneNode(true);
    const originals = [el, ...el.querySelectorAll('*')];
    const clones = [copy, ...copy.querySelectorAll('*')];
    originals.forEach((node, i) => {
      if (!safe(node)) {
        clones[i].textContent = '';
        for (const attr of [...clones[i].attributes]) clones[i].removeAttribute(attr.name);
      }
    });
    copy.querySelectorAll('script,style,noscript,input[type=hidden]')
      .forEach(node => node.remove());
    return copy;
  };
  const name = el => norm(el.getAttribute('aria-label') ||
    (live ? (el.getAttribute('aria-labelledby') || '').split(/\s+/)
      .map(id => {
        const label = document.getElementById(id);
        return label && safe(label) ? label.textContent : '';
      }).join(' ') : '') ||
    [...(el.labels || [])].filter(safe).map(x => x.textContent).join(' ') ||
    (el.matches('button,a,[role=button],[role=tab],[role=menuitem],[role=option]')
      ? el.innerText : el.type === 'submit' ? el.value : '') ||
    (live ? el.getAttribute('placeholder') || el.getAttribute('data-placeholder') ||
      el.getAttribute('title') : ''));
  const request = el => {
    if (el.matches('a[href]')) {
      if ((el.target && el.target !== '_self') || el.hasAttribute('download') ||
          el.hasAttribute('ping')) return null;
      return {url:el.href,method:'GET',body:''};
    }
    const form = el.form;
    if (!form || el.type !== 'submit' || (form.target && form.target !== '_self')) return null;
    if (['formaction','formmethod','formenctype','formtarget'].some(x => el.hasAttribute(x)))
      return null;
    if (form.enctype !== 'application/x-www-form-urlencoded' ||
        !['get','post'].includes(form.method)) return null;
    const fields = [];
    for (const f of form.elements) {
      if (f.matches(':disabled') || f.matches('button,input[type=submit]')) continue;
      if (!safe(f) || !f.matches('textarea,input[type=text],input:not([type])') ||
          !f.name || f.hasAttribute('dirname')) return null;
      if (f.value.length > 4000 || fields.length >= 20) return null;
      fields.push([f.name,f.value.replace(/\r\n|\r|\n/g,'\r\n')]);
    }
    if (el.name) fields.push([el.name,el.value]);
    const body = new URLSearchParams(fields).toString();
    const url = new URL(form.action);
    if (form.method === 'get') url.search = body;
    return {url:url.href,method:form.method.toUpperCase(),
      body:form.method === 'post' ? body : '',
      fields:fields.map(([name,value]) => ({name,value}))};
  };
  const all = [...document.querySelectorAll(selector)].slice(0,50000);
  const elements = [];
  for (const [index,el] of all.entries()) {
    if (!safe(el) || !el.getClientRects().length || el.matches(':disabled') ||
        el.getAttribute('aria-disabled') === 'true' ||
        el.getAttribute('aria-readonly') === 'true' ||
        getComputedStyle(el).visibility === 'hidden' || el.readOnly) continue;
    const role = el.getAttribute('role') || (el.matches('a') ? 'link' :
      el.matches('button,input[type=submit]') ? 'button' :
      el.matches('input[type=search]') ? 'searchbox' : 'textbox');
    if (!['link','button','textbox','searchbox','tab','menuitem','combobox','option']
        .includes(role))
      continue;
    const label = name(el) ||
      (live ? (editable(el) ? 'Text editor ' : 'Element ') + (index+1) : '');
    const html = safeClone(el).outerHTML;
    const value = editable(el) ? (el.value ?? el.innerText ?? '') : '';
    if (!label || label.length > 300 || html.length > 100000 || value.length > 4000) continue;
    elements.push({index,role,name:label,html,value,request:request(el)});
    if (elements.length >= (live ? 96 : 50)) break;
  }
  // Password/OTP values are not read even for hashes. Body text skips private/hidden nodes.
  const states = [...document.querySelectorAll('input:not([type=hidden]),textarea,select')]
    .filter(safe)
    .map(el => [el.value,el.checked,el.selectedIndex]);
  const walker = document.createTreeWalker(document.body || document.documentElement,
    NodeFilter.SHOW_TEXT);
  let node,text = '',count = 0;
  while ((node = walker.nextNode()) && text.length < 12000 && count++ < 50000) {
    const parent = node.parentElement;
    if (parent && safe(parent) && !parent.closest('script,style,noscript') &&
        parent.getClientRects().length && getComputedStyle(parent).visibility !== 'hidden') {
      text += norm(node.textContent) + '\n';
    }
  }
  text = text.slice(0,12000);
  const material = live ? JSON.stringify([document.title,text,elements,states,
    all.indexOf(document.activeElement)]) :
    safeClone(document.documentElement).outerHTML + JSON.stringify(states);
  return {material:material.length <= 2000000 ? material : '',title:document.title.slice(0,500),
    text,elements,has_unsaved_changes:
      [...document.querySelectorAll('input,textarea')].filter(safe)
        .some(el => el.value !== el.defaultValue) ||
      [...document.querySelectorAll('[contenteditable]')].filter(safe)
        .some(el => Boolean(el.innerText))};
}""".replace("__SAFE__", SAFE_ELEMENT)
    .replace("__LIVE__", json.dumps(LIVE_SELECTOR))
    .replace("__STRICT__", json.dumps(STRICT_SELECTOR))
)
