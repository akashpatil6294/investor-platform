/* Dashboard rendering uses DOM nodes; data and model text never enter innerHTML. */
(() => {
  const $=id=>document.getElementById(id), el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined&&text!==null)n.textContent=text;if(cls)n.className=cls;return n;};
  const fields={revenue:'Revenue',operating_income:'Operating income',net_income:'Net income',cash_flow:'Operating cash flow',total_assets:'Total assets',total_liabilities:'Total liabilities'};
  const ratioLabels={operating_margin:'Operating margin',net_margin:'Net margin',liabilities_to_assets:'Liabilities / assets',cash_conversion:'Cash conversion'};
  const tickers={Apple:'AAPL',Amazon:'AMZN',Microsoft:'MSFT',Tesla:'TSLA'};
  let notes={}, rows=[], selected=new Set(), active=null, year=2024, insightToken=0;
  const num=v=>v===null||v===undefined||v===''?null:Number.isFinite(Number(v))?Number(v):null;
  const money=v=>num(v)===null?'Not reported':'$'+Number(v).toLocaleString('en-US',{maximumFractionDigits:0})+'M';
  const fullMoney=v=>num(v)===null?'Not reported':money(v)+' ($'+(Number(v)/1000).toLocaleString('en-US',{minimumFractionDigits:1,maximumFractionDigits:1})+'B)';
  function toast(text){$('toast').textContent=text;$('toast').hidden=false;setTimeout(()=>$('toast').hidden=true,6000);}
  function current(company,y=year){return rows.find(r=>r.company===company&&Number(r.year)===Number(y));}
  function companies(){return [...new Set(rows.map(r=>r.company))].sort((a,b)=>Number(notes[b]?.pinned||false)-Number(notes[a]?.pinned||false)||a.localeCompare(b));}
  function source(company){return company==='Amazon'&&year===2025?'2025_AMZN.pdf':`${year}_${company}.pdf`;}
  function openSource(company,field){const evidence=current(company)?.metric_sources?.[field];window.open('/api/source/'+encodeURIComponent(evidence?.document||source(company))+(evidence?.page?'#page='+evidence.page:''),'_blank','noopener');}
  async function health(){try{const p=await API.getHealth();$('headerStatusText').textContent=p.status;}catch(e){$('headerStatusText').textContent=e.code==='timeout'?'Status timed out':'Server unavailable';}}
  function coverage(){const list=$('coverageList');list.replaceChildren();companies().forEach(c=>{
    const row=el('div',null,'coverage-row'),check=el('input');check.type='checkbox';check.checked=selected.has(c);check.setAttribute('aria-label','Compare '+c);
    check.onchange=()=>{if(check.checked&&selected.size>=4){check.checked=false;toast('Select up to four companies.');return;}check.checked?selected.add(c):selected.delete(c);renderTable();chart();};
    const button=el('button');button.type='button';button.append(el('div',c),el('span',tickers[c]||c.slice(0,4).toUpperCase(),'ticker'));button.setAttribute('aria-current',String(c===active));button.onclick=()=>choose(c);row.append(check,button);list.append(row);
  });}
  function spark(prior,value,label){const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 50 14');svg.setAttribute('width','50');svg.setAttribute('height','14');svg.setAttribute('role','img');svg.setAttribute('aria-label',label);const line=document.createElementNS(svg.namespaceURI,'path');line.setAttribute('d',`M2 ${prior>value?2:12} L48 ${prior>value?12:2}`);line.setAttribute('stroke','var(--muted)');line.setAttribute('fill','none');svg.append(line);return svg;}
  function renderTable(){
    const box=$('comparison');box.replaceChildren();const names=[...selected];
    if(!names.length){box.append(el('p','Select companies in the coverage list to build a comparison.','skeleton'));return;}
    const table=el('table'),caption=el('caption',`Reported financials for FY${year}, USD millions`,'sr-only');table.append(caption);
    const head=el('thead'),hr=el('tr');const corner=el('th','Reported metric');corner.scope='col';hr.append(corner);names.forEach(c=>{const th=el('th',c);th.scope='col';hr.append(th);});head.append(hr);table.append(head);const body=el('tbody');
    Object.entries(fields).forEach(([field,label])=>{
      const tr=el('tr'),th=el('th',label);th.scope='row';tr.append(th);
      const vals=names.map(c=>num(current(c)?.[field])).filter(v=>v!==null);
      const best=vals.length?(field==='total_liabilities'?Math.min(...vals):Math.max(...vals)):null;
      names.forEach(c=>{const row=current(c),value=num(row?.[field]),td=el('td');if(value!==null&&value===best)td.className='best';
        const b=el('button',money(value));b.title=`${fullMoney(value)} — open ${c} annual report`;b.onclick=()=>openSource(c,field);td.append(b);
        if(value!==null)td.append(el('small','($'+(value/1000).toLocaleString('en-US',{minimumFractionDigits:1,maximumFractionDigits:1})+'B)'));
        if(row?.checks?.[field]){const badge=el('span','check','check');badge.title=row.checks[field];badge.setAttribute('aria-label',row.checks[field]);td.append(badge);}
        const old=num(current(c,year-1)?.[field]);if(value!==null&&old!==null&&old!==0){const delta=(value-old)/Math.abs(old)*100;td.append(el('small',`${delta>=0?'+':''}${delta.toFixed(1)}% YoY`,delta>=0?'positive':'negative'));td.append(spark(old,value,`${c} ${label}: ${money(old)} to ${money(value)}`));}
        tr.append(td);
      });body.append(tr);
    });table.append(body);box.append(table);$('dataFootnote').textContent=`FY${year}, USD millions, source: annual report. Shading marks highest values (lowest liabilities), not an investment ranking.`;
  }
  function svgNode(tag,attrs,text){const n=document.createElementNS('http://www.w3.org/2000/svg',tag);Object.entries(attrs).forEach(([k,v])=>n.setAttribute(k,v));if(text)n.textContent=text;return n;}
  function chart(){
    const field=$('metricSelect').value||'revenue',names=[...selected],box=$('chart');box.replaceChildren();$('chartTitle').textContent=fields[field]+' comparison';
    const points=names.map(c=>({c,v:num(current(c)?.[field]),prior:num(current(c,year-1)?.[field])}));
    const values=points.flatMap(p=>[p.v,p.prior]).filter(v=>v!==null);
    if(!values.length){box.append(el('p','No reported values for this selection.','empty-state'));return;}
    const min=Math.min(0,...values),max=Math.max(0,...values)||1,plot=460,scale=v=>140+(v-min)/(max-min)*plot,zero=scale(0),height=points.length*62+40;
    const svg=svgNode('svg',{viewBox:`0 0 740 ${height}`,role:'img','aria-label':`${fields[field]} in USD millions, FY${year}`});
    svg.append(svgNode('line',{x1:zero,y1:8,x2:zero,y2:height-24,stroke:'var(--line)'}));
    points.forEach((p,i)=>{const y=20+i*62;svg.append(svgNode('text',{x:0,y:y+16,fill:'var(--text)','font-size':14,'font-family':'Segoe UI, sans-serif'},p.c));
      if(p.v!==null){const rect=svgNode('rect',{x:Math.min(zero,scale(p.v)),y,width:Math.max(1,Math.abs(scale(p.v)-zero)),height:20,fill:'var(--accent)',rx:2});rect.append(svgNode('title',{},`${p.c} FY${year}: ${fullMoney(p.v)}`));svg.append(rect);svg.append(svgNode('text',{x:610,y:y+15,fill:'var(--text)','font-size':12,'font-family':'Consolas, monospace'},money(p.v)));}
      if(p.prior!==null){svg.append(svgNode('rect',{x:Math.min(zero,scale(p.prior)),y:y+25,width:Math.max(1,Math.abs(scale(p.prior)-zero)),height:12,fill:'var(--muted)',opacity:.5,rx:2}));}
    });box.append(svg);$('chartNote').textContent=points.some(p=>p.prior!==null)?`Accent: FY${year}. Grey: FY${year-1}. USD millions.`:`FY${year} · USD millions. Prior-year reports have not been added.`;
  }
  function renderInsights(target,items,filename){const box=$(target);box.replaceChildren();
    if(!Array.isArray(items)||!items.length){box.append(el('p','No evidence-backed items available.','muted'));return;}
    items.forEach(item=>{const div=el('article',null,'insight');if(typeof item==='string'){div.append(el('p',item));}else{div.append(el('h4',item.title),el('p',item.detail));if(item.evidence_page){const a=el('a',`Annual report · page ${item.evidence_page}`);a.href='/api/source/'+encodeURIComponent(filename)+`#page=${item.evidence_page}`;a.target='_blank';a.rel='noopener';div.append(a);}}box.append(div);});
  }
  async function detail(){
    const row=current(active);$('detailTitle').textContent=active?`${active} · FY${year}`:'Company notebook';const grid=$('ratioGrid');grid.replaceChildren();
    Object.entries(ratioLabels).forEach(([key,label])=>{const d=el('div',null,'ratio'),v=row?.derived?.[key];d.append(el('small',label),el('strong',v===null||v===undefined?'—':key==='cash_conversion'?v.toFixed(2)+'×':(v*100).toFixed(1)+'%'),el('small','Derived'));grid.append(d);});
    const token=++insightToken;$('growthList').textContent='Loading evidence…';$('riskList').textContent='Loading evidence…';$('insightStatus').textContent='';
    if(!active)return;
    try{const result=await API.request(`/api/insights/${encodeURIComponent(active)}/${year}`);if(token!==insightToken)return;
      renderInsights('growthList',result.payload?.growth_drivers,result.source);renderInsights('riskList',result.payload?.risk_factors,result.source);$('insightStatus').textContent=result.version?`Evidence checked against PDF pages · ${result.version}`:'No refreshed insights for this report.';
    }catch(e){if(token!==insightToken)return;renderInsights('growthList',[],null);renderInsights('riskList',[],null);$('insightStatus').textContent=e.message;}
  }
  function choose(c){active=c;$('companyNote').value=notes[c]?.note||'';$('pinCompany').checked=notes[c]?.pinned||false;$('noteStatus').textContent='';$('companySelect').value=c;coverage();detail();ResearchAssistant.setScope(c,year);$('sidebar').classList.remove('open');$('menuToggle').setAttribute('aria-expanded','false');}
  async function load(){
    try{const response=await API.getMetrics();rows=response.rows;const names=companies();selected=new Set(names.slice(0,4));active=names.includes(active)?active:names[0];
      $('dataError').hidden=true;const years=[...new Set(rows.map(r=>Number(r.year)))].sort((a,b)=>b-a);if(years.length&&!years.includes(year))year=years[0];$('yearSelect').replaceChildren();years.forEach(y=>{const o=el('option',y);o.value=y;$('yearSelect').append(o);});$('yearSelect').value=year;
      $('companySelect').replaceChildren();names.forEach(c=>{const o=el('option',c);o.value=c;$('companySelect').append(o);});
      if(!names.length){$('comparison').replaceChildren(el('p','No reports yet. Add an annual-report PDF to start.','skeleton'));return;}
      coverage();renderTable();chart();choose(active);
    }catch(e){const box=$('dataError');box.hidden=false;box.replaceChildren(el('p',e.message));const retry=el('button','Retry');retry.onclick=load;box.append(retry);$('comparison').replaceChildren();$('coverageList').textContent='Coverage unavailable.';}
  }
  function download(blob,name){const a=el('a');a.href=URL.createObjectURL(blob);a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}
  function exportCsv(){const names=[...selected],quote=v=>'"'+String(v??'').replaceAll('"','""')+'"';
    const data=[['Metric (USD millions)',...names],...Object.entries(fields).map(([k,v])=>[v,...names.map(c=>current(c)?.[k]??'')])];
    download(new Blob(['\ufeff'+data.map(r=>r.map(quote).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'}),`comparison-FY${year}.csv`);
  }
  async function exportPng(){const svg=$('chart').querySelector('svg');if(!svg){toast('Choose a metric with data first.');return;}
    const clone=svg.cloneNode(true),style=getComputedStyle(document.documentElement);clone.querySelectorAll('*').forEach(n=>['fill','stroke'].forEach(a=>{const v=n.getAttribute(a);if(v?.startsWith('var('))n.setAttribute(a,style.getPropertyValue(v.slice(4,-1)).trim());}));
    clone.setAttribute('xmlns','http://www.w3.org/2000/svg');const view=svg.viewBox.baseVal;clone.setAttribute('width',view.width);clone.setAttribute('height',view.height);
    const url=URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(clone)],{type:'image/svg+xml'})),img=new Image();
    img.onload=()=>{const canvas=document.createElement('canvas');canvas.width=view.width*2;canvas.height=view.height*2;const ctx=canvas.getContext('2d');ctx.fillStyle=style.getPropertyValue('--surface');ctx.fillRect(0,0,canvas.width,canvas.height);ctx.drawImage(img,0,0,canvas.width,canvas.height);canvas.toBlob(blob=>{if(blob)download(blob,`comparison-${year}.png`);});URL.revokeObjectURL(url);};img.onerror=()=>{URL.revokeObjectURL(url);toast('Could not export the chart.');};img.src=url;
  }
  async function ingest(file){
    if(!file)return;if(!file.name.toLowerCase().endsWith('.pdf')||file.size>25*1024*1024){toast('Choose a PDF no larger than 25 MB.');return;}
    const progress=$('uploadProgress');progress.hidden=false;progress.value=0;$('fileInput').disabled=true;
    try{const data=new FormData();data.append('file',file);const job=await new Promise((resolve,reject)=>{const xhr=new XMLHttpRequest();xhr.open('POST','/api/ingest');xhr.timeout=60000;xhr.upload.onprogress=e=>{if(e.lengthComputable)progress.value=e.loaded/e.total*100;};xhr.onload=()=>{let p;try{p=JSON.parse(xhr.responseText);}catch{return reject(new Error('Invalid upload response.'));}xhr.status<300?resolve(p):reject(new Error(p.detail||p.message||'Upload failed.'));};xhr.onerror=()=>reject(new Error('Upload connection failed.'));xhr.ontimeout=()=>reject(new Error('Upload timed out.'));xhr.send(data);});
      progress.removeAttribute('value');let status;
      for(let attempt=0;attempt<600;attempt++){status=await API.request('/api/ingest/'+job.job_id);$('ingestLog').replaceChildren(...(status.log||[]).slice(-5).map(line=>el('p',line)));
        if(status.status==='completed'){toast(`${status.company} report is ready.`);await load();return;}if(status.status==='failed')throw new Error(status.error||'Ingestion failed.');await new Promise(r=>setTimeout(r,2000));}
      toast('Ingestion is still running. Check again from the job-status endpoint.');
    }catch(e){toast(e.message);$('ingestLog').textContent=e.message;}finally{progress.hidden=true;$('fileInput').disabled=false;$('fileInput').value='';}
  }
  function palette(){const dialog=$('commandDialog');dialog.showModal();$('commandInput').value='';commands('');$('commandInput').focus();}
  function commands(query){const box=$('commandResults');box.replaceChildren();companies().filter(c=>c.toLowerCase().includes(query.toLowerCase())).forEach(c=>{const b=el('button','Open '+c);b.onclick=()=>{$('commandDialog').close();choose(c);$('companyDetail').scrollIntoView();};box.append(b);});
    if(query){const b=el('button','Ask: '+query);b.onclick=()=>{$('commandDialog').close();ResearchAssistant.submit(query);};box.append(b);}}
  function init(){
    ResearchAssistant.init();Object.entries(fields).forEach(([k,v])=>{const o=el('option',v);o.value=k;$('metricSelect').append(o);});
    $('metricSelect').onchange=chart;$('yearSelect').onchange=e=>{year=Number(e.target.value);renderTable();chart();detail();ResearchAssistant.setScope(active,year);};$('companySelect').onchange=e=>choose(e.target.value);
    $('headerStatus').onclick=health;$('themeToggle').onclick=()=>{const dark=document.documentElement.dataset.theme!=='dark';document.documentElement.dataset.theme=dark?'dark':'light';$('themeToggle').textContent=dark?'Switch to light':'Switch to dark';try{localStorage.setItem('ip.theme',dark?'dark':'light');}catch{}};
    try{if(localStorage.getItem('ip.theme')==='dark')$('themeToggle').click();}catch{}
    $('exportCsv').onclick=exportCsv;$('exportPng').onclick=exportPng;$('printSummary').onclick=()=>window.print();
    $('compareTakeaway').onclick=()=>{if(selected.size<2){toast('Choose at least two companies.');return;}ResearchAssistant.submit(`Compare ${[...selected].join(' vs ')} ${fields[$('metricSelect').value].toLowerCase()} in ${year}`);};
    $('fileInput').onchange=e=>ingest(e.target.files[0]);$('menuToggle').onclick=()=>{const open=$('sidebar').classList.toggle('open');$('menuToggle').setAttribute('aria-expanded',String(open));};
    $('commandOpen').onclick=palette;$('commandInput').oninput=e=>commands(e.target.value);document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key==='k'||e.key==='/'&&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)){e.preventDefault();palette();}if(e.key==='Escape'){$('sidebar').classList.remove('open');$('menuToggle').setAttribute('aria-expanded','false');}});
    $('noteForm').onsubmit=async e=>{e.preventDefault();if(!active)return;const target=active;const note={company:target,note:$('companyNote').value,pinned:$('pinCompany').checked};$('saveNote').disabled=true;try{await API.request('/api/notes',{method:'POST',body:JSON.stringify(note)});notes[target]=note;$('noteStatus').textContent=' Saved';coverage();}catch(err){$('noteStatus').textContent=err.message;}finally{$('saveNote').disabled=false;}};
    API.request('/api/notes').then(p=>{notes=Object.fromEntries(p.data.map(n=>[n.company,n]));if(active)choose(active);}).catch(e=>{$('noteStatus').textContent=e.message;});
    load();health();setInterval(health,30000);
  }
  init();
})();
