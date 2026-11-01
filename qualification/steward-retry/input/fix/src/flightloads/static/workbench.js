// Glass-box station workbench (plan section 3.1). Every number shown comes from /api/workbench,
// which runs the production modules; this page only edits inputs and draws results.
'use strict';
const wb={stations:[],defaults:{},inputs:{},results:{},current:null,saved:[],busy:false,plantRun:null,plantRunSnapshot:null,cursor:0,freezeTimer:null,options:{atmosphere:false,propulsion:false}};

function el(tag,attrs,...children){const e=document.createElement(tag);for(const [k,v] of Object.entries(attrs||{})){if(v==null||v===false)continue;if(k==='class')e.className=v;else if(k==='text')e.textContent=v;else if(k.startsWith('on'))e.addEventListener(k.slice(2),v);else e.setAttribute(k,v===true?'':v);}for(const c of children.flat()){if(c==null)continue;e.appendChild(typeof c==='string'?document.createTextNode(c):c);}return e;}
async function api(path,body){const opt=body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)};const r=await fetch('/api/workbench'+path,opt);const text=await r.text();let data;try{data=JSON.parse(text);}catch{data=text;}if(!r.ok)throw Error(data&&data.error?data.error:('HTTP '+r.status));return data;}
const clone=v=>JSON.parse(JSON.stringify(v));
const fmt=(v,d=4)=>v==null?'—':typeof v==='number'?(Math.abs(v)>=1e5||(Math.abs(v)<1e-3&&v!==0)?v.toExponential(3):(+v.toPrecision(d+2)).toString()):String(v);
function humanize(key){const units={ft:'ft',ft_s:'ft/s',lbf:'lbf',deg:'deg',R:'degR',s:'s',in:'in',deg_s:'deg/s',ft_lbf_s:'ft·lbf/s',B_deg_s:'deg/s, body'};const parts=key.split('_');for(let n=3;n>=1;n--){const tail=parts.slice(-n).join('_');if(parts.length>n&&units[tail])return parts.slice(0,-n).join(' ')+' ('+units[tail]+')';}return parts.join(' ');}

// ---------------------------------------------------------------- station 1 polar helpers (#266)
// Hover text only attaches to scatter points in plotLines, so every polar gets a point series carrying these labels.
function secPolarLabels(alpha,cl,cd,cm){return alpha.map((a,i)=>a==null||cl[i]==null||cd[i]==null||cm[i]==null?null:`α=${fmt(a)}°, CL=${fmt(cl[i])}, CD=${fmt(cd[i])}, CM=${fmt(cm[i])}`);}
function secFiveDegreeLabels(alpha,cl,cd){const x=[],y=[],labels=[];alpha.forEach((a,i)=>{if(a==null||cl[i]==null||cd[i]==null||Math.abs(a/5-Math.round(a/5))>1e-9)return;x.push(cl[i]);y.push(cd[i]);labels.push(`α=${fmt(a)}°`);});return {x,y,labels};}
function secNegativeLiftSummary(alpha,cl,nonconverged){
  let min=null,max=null;alpha.forEach((a,i)=>{if(a==null||cl[i]==null)return;if(!min||cl[i]<min.cl)min={cl:cl[i],alpha:a};if(!max||cl[i]>max.cl)max={cl:cl[i],alpha:a};});
  const negativeGaps=(nonconverged||[]).filter(a=>a<0);
  return {min,max,hasNegativeLift:!!min&&min.cl<0,negativeGaps};}
function secNegativeLiftText(summary){
  if(!summary.min)return 'No converged points.';
  const gaps=summary.negativeGaps.length?`${summary.negativeGaps.length} negative-α angles did not converge (${summary.negativeGaps.join(', ')} deg), shown as gaps.`:'All requested negative-α angles converged.';
  return (summary.hasNegativeLift?`Converged CL spans ${fmt(summary.min.cl)} at α=${fmt(summary.min.alpha)}° to ${fmt(summary.max.cl)} at α=${fmt(summary.max.alpha)}°. `:`No negative lift in the converged range (lowest CL ${fmt(summary.min.cl)} at α=${fmt(summary.min.alpha)}°); start the α range lower to see the negative-lift branch. `)+gaps;}
function secFlightRange(launchResult){const alphas=(launchResult?.truth||[]).map(r=>r.alpha_deg).filter(a=>a!=null&&isFinite(a));return alphas.length?{min:Math.min(...alphas),max:Math.max(...alphas)}:null;}

// ---------------------------------------------------------------- generic input forms
function numberInput(id,value,onValue,integer){return el('input',{type:'number',id,step:integer?'1':'any',value:String(value),oninput:e=>{const v=e.target.value.trim();onValue(v===''?NaN:integer?parseInt(v,10):Number(v));}});}
function buildForm(obj,prefix,readonly){
  const box=el('div');
  for(const [key,value] of Object.entries(obj)){
    const id=prefix+'-'+key;
    if(value!==null&&typeof value==='object'&&!Array.isArray(value)){box.appendChild(el('fieldset',{},el('legend',{text:humanize(key)}),buildForm(value,id,readonly)));}
    else if(Array.isArray(value)&&value.length===3&&value.every(v=>typeof v==='number')){box.appendChild(el('label',{class:'wb-field wb-vec'},humanize(key),...value.map((v,i)=>numberInput(id+'-'+i,v,x=>{value[i]=x;},false))));}
    else if(typeof value==='number'){box.appendChild(el('label',{class:'wb-field'},humanize(key),numberInput(id,value,x=>{obj[key]=x;},key==='seed'||key==='schema_version')));}
    else if(typeof value==='string'){const fixed=['mode','model','thrust_mode','schema'].includes(key);box.appendChild(el('label',{class:'wb-field'},humanize(key),el('input',{type:'text',id,value,readonly:fixed,oninput:fixed?null:e=>{obj[key]=e.target.value;}})));}
    else if(typeof value==='boolean'){box.appendChild(el('label',{class:'wb-field'},humanize(key),el('input',{type:'checkbox',id,checked:value,onchange:e=>{obj[key]=e.target.checked;}})));}
  }
  return box;
}
function card(title,...children){return el('section',{class:'wb-card'},title?el('h3',{text:title}):null,...children);}
function table(headers,rows,id){return el('div',{class:'wb-scroll'},el('table',{class:'wb-table',id},el('thead',{},el('tr',{},...headers.map(h=>el('th',{text:h})))),el('tbody',{},...rows.map(r=>el('tr',{},...r.map(c=>el('td',{text:typeof c==='number'||c==null?fmt(c):String(c)})))))));}
function plotCard(spec,id){const box=el('div',{id});plotLines(box,spec);return box;}
function badge(level){return level?el('span',{class:'wb-badge '+level,id:'wb-evidence',text:'evidence: '+level}):null;}

// ---------------------------------------------------------------- concept notes (safe markdown subset)
function inline(text){const out=[];const re=/(`[^`]+`|\*\*[^*]+\*\*)/g;let last=0,m;while((m=re.exec(text))){if(m.index>last)out.push(text.slice(last,m.index));const t=m[0];out.push(t[0]==='`'?el('code',{text:t.slice(1,-1)}):el('strong',{text:t.slice(2,-2)}));last=m.index+t.length;}if(last<text.length)out.push(text.slice(last));return out;}
function renderMarkdown(md){
  const root=el('div');const lines=md.replace(/\r/g,'').split('\n');let i=0;
  while(i<lines.length){const line=lines[i];
    if(line.startsWith('```')){const buf=[];i++;while(i<lines.length&&!lines[i].startsWith('```'))buf.push(lines[i++]);i++;root.appendChild(el('pre',{text:buf.join('\n')}));continue;}
    const h=line.match(/^(#{1,4})\s+(.*)$/);if(h){root.appendChild(el('h'+Math.min(6,h[1].length+2),{},...inline(h[2])));i++;continue;}
    if(line.startsWith('|')){const rows=[];while(i<lines.length&&lines[i].startsWith('|'))rows.push(lines[i++]);const cells=r=>r.replace(/^\||\|$/g,'').split('|').map(c=>c.trim());const body=rows.filter(r=>!/^\|[\s:|-]+\|?$/.test(r));root.appendChild(el('div',{class:'wb-scroll'},el('table',{class:'wb-table'},el('thead',{},el('tr',{},...cells(body[0]).map(c=>el('th',{},...inline(c))))),el('tbody',{},...body.slice(1).map(r=>el('tr',{},...cells(r).map(c=>el('td',{},...inline(c)))))))));continue;}
    if(/^\s*([-*]|\d+\.)\s+/.test(line)){const ordered=/^\s*\d+\./.test(line);const list=el(ordered?'ol':'ul');while(i<lines.length&&/^\s*([-*]|\d+\.)\s+/.test(lines[i])){let item=lines[i++].replace(/^\s*([-*]|\d+\.)\s+/,'');while(i<lines.length&&/^\s{2,}\S/.test(lines[i])&&!/^\s*([-*]|\d+\.)\s+/.test(lines[i]))item+=' '+lines[i++].trim();list.appendChild(el('li',{},...inline(item)));}root.appendChild(list);continue;}
    if(/^\s{4}\S/.test(line)){const buf=[];while(i<lines.length&&(/^\s{4}/.test(lines[i])||lines[i]===''))buf.push(lines[i++].slice(4));root.appendChild(el('pre',{text:buf.join('\n').trimEnd()}));continue;}
    if(line.trim()===''){i++;continue;}
    const buf=[line];i++;while(i<lines.length&&lines[i].trim()!==''&&!/^(#|```|\||\s*([-*]|\d+\.)\s|\s{4}\S)/.test(lines[i]))buf.push(lines[i++]);root.appendChild(el('p',{},...inline(buf.join(' '))));}
  return root;
}
async function notesCard(station){
  const box=el('div',{class:'wb-note',id:'wb-note-'+station.id});
  if(!station.notes.length){box.textContent='Concept note arrives with issue #'+station.issue+'.';return card('Concept note',box);}
  const tabs=el('div',{class:'wb-tabs'});const body=el('div');
  const show=async name=>{for(const b of tabs.children)b.setAttribute('aria-pressed',String(b.dataset.name===name));body.textContent='Loading…';try{const r=await fetch('/api/workbench/notes/'+name);if(!r.ok)throw Error('HTTP '+r.status);body.textContent='';body.appendChild(renderMarkdown(await r.text()));}catch(e){body.textContent='Note unavailable: '+e.message;}};
  for(const n of station.notes)tabs.appendChild(el('button',{'data-name':n,'aria-pressed':'false',onclick:()=>show(n),text:n.replace('.md','')}));
  box.append(tabs,body);show(station.notes[0]);
  return card('Concept note (docs/mechanics)',box);
}

// ---------------------------------------------------------------- run / save plumbing
function setBusy(on){wb.busy=on;for(const b of document.querySelectorAll('button.wb-btn'))b.disabled=on;}
function showError(id,error){const e=document.getElementById('wb-error-'+id);if(e)e.textContent=error?String(error.message||error):'';}
async function runStation(sid,runId,body){showError(sid,null);setBusy(true);const status=document.getElementById('wb-status-'+sid);if(status)status.textContent='Running…';
  try{const r=await api('/run/'+runId,body);if(status)status.textContent='Completed '+new Date().toLocaleTimeString();return r;}
  catch(e){showError(sid,e);if(status)status.textContent='Failed';return null;}finally{setBusy(false);}}
async function saveStation(sid,runId,body){const label=(document.getElementById('wb-label-'+sid)||{}).value||'';showError(sid,null);setBusy(true);
  try{const rec=await api('/saved',{station:runId,label,inputs:body});const s=document.getElementById('wb-status-'+sid);if(s)s.textContent='Saved '+rec.id+' (inputs '+rec.provenance.inputs_sha256.slice(0,12)+'…)';await refreshSaved(sid);return rec;}
  catch(e){showError(sid,e);return null;}finally{setBusy(false);}}
async function refreshSaved(sid){const box=document.getElementById('wb-saved-'+sid);if(!box)return;try{wb.saved=(await api('/saved')).runs;}catch(e){box.textContent='Saved runs unavailable: '+e.message;return;}
  const mine=wb.saved.filter(r=>r.station===sid||(sid==='plant'&&r.station==='plant-run'));box.textContent='';
  if(!mine.length){box.appendChild(el('p',{class:'wb-status',text:'No saved runs yet.'}));return;}
  box.appendChild(table(['saved (UTC)','label','run','inputs sha256'],mine.slice(0,20).map(r=>[r.created_utc,r.label||'—',r.station,r.provenance.inputs_sha256.slice(0,16)]),'wb-saved-table-'+sid));
  const pick=el('select',{id:'wb-saved-pick-'+sid},...mine.slice(0,20).map(r=>el('option',{value:r.id,text:r.created_utc+' '+(r.label||r.station)})));
  box.appendChild(el('div',{class:'wb-actions'},pick,el('button',{class:'wb-btn wb-secondary',id:'wb-load-'+sid,onclick:()=>loadSaved(sid,pick.value)},'Open saved run')));}
async function loadSaved(sid,identifier){try{const rec=await api('/saved/'+identifier);
  if(rec.station==='plant-run'){wb.inputs.plant.controls=clone(rec.inputs.controls);wb.inputs.plant.run=clone(rec.inputs.run);setPlantRun(rec.result,rec.inputs);}
  else if(rec.station==='plant'){wb.inputs.plant.controls=clone(rec.inputs.controls);if(rec.inputs.state)wb.inputs.plant.state=clone(rec.inputs.state);wb.results.plant=rec.result;}
  else if(rec.station==='launch'){wb.inputs.launch=clone(rec.inputs);setLaunchRun(rec.result,rec.inputs);}
  else if(rec.station==='loads'){stopPlayback('loads');clearTimeout(wb.freezeTimer);wb.loadsRequestToken=(wb.loadsRequestToken||0)+1;wb.savedLoadsRecord=clone(rec);wb.results.loads=wb.savedLoadsRecord.result;}
  else{wb.inputs[sid]=clone(rec.inputs);wb.results[sid]=rec.result;}
  render();if(rec.station==='plant-run')freezeAtCursor();if(rec.station==='launch'&&wb.current==='loads')evaluateLoads();
}catch(e){showError(sid,e);}}
function actions(sid,buttons){return el('div',{},el('div',{class:'wb-actions'},...buttons),el('label',{class:'wb-field'},'Label for saved run',el('input',{type:'text',id:'wb-label-'+sid,maxlength:'120'})),el('div',{class:'wb-status',id:'wb-status-'+sid}),el('div',{class:'wb-error',id:'wb-error-'+sid,role:'alert'}));}

// ---------------------------------------------------------------- station 1: aero sections
function sectionPlotSamples(polar){const indices=polar.requested_alpha_deg.map(a=>polar.converged_alpha_deg.findIndex(v=>Math.abs(v-a)<5e-4));return Object.fromEntries(['CL','CD','CDp','CM','Top_Xtr','Bot_Xtr'].map(k=>[k,indices.map(i=>i<0?null:polar[k][i])]));}
function sectionsView(){
  const sid='sections',inputs=wb.inputs[sid],r=wb.results[sid];
  const form=buildForm(inputs,'wb-in-'+sid),foil=form.querySelector('#wb-in-sections-airfoil');foil.removeAttribute('readonly');foil.addEventListener('input',e=>{inputs.airfoil=e.target.value;});
  const left=el('div',{},card('Inputs',form,el('label',{class:'wb-field'},'Enable flap',el('input',{id:'wb-in-sections-flap-enabled',type:'checkbox',checked:inputs.flap.enabled,onchange:e=>{inputs.flap.enabled=e.target.checked;}})),
      el('p',{class:'wb-status',text:'Airfoil: NACA 4-digit (for example NACA 0009, 2412, 4412). NACA 0009 and 4412 have the NACA Report 824 reference at Re 3e6.'}),
      actions(sid,[el('button',{class:'wb-btn',id:'wb-run-'+sid,onclick:async()=>{const x=await runStation(sid,'sections',inputs);if(x){wb.results[sid]=x;render();}}},'Run XFOIL'),
        el('button',{class:'wb-btn wb-secondary',id:'wb-save-'+sid,onclick:async()=>{const rec=await saveStation(sid,'sections',inputs);if(rec){wb.results[sid]=rec.result;render();}}},'Run and save')])),
    card('Saved runs',el('div',{id:'wb-saved-'+sid})));
  const out=el('div',{id:'wb-result-'+sid});
  if(r){const P=r.polar,Q=sectionPlotSamples(P),a=P.requested_alpha_deg,c=r.characteristics,ref=r.reference,gaps=P.nonconverged_alpha_deg;
    const clMin=P.CL.length?Math.min(...P.CL):0;const gapSeries={name:'not converged',scatter:true,x:gaps,y:gaps.map(()=>clMin),labels:gaps.map(g=>'XFOIL did not converge at '+g+' deg')};
    // Hover labels for all points on CD(CL), CL(alpha), CM(alpha)
    const hoverLabels=secPolarLabels(a,Q.CL,Q.CD,Q.CM);
    const clSeries=[{name:'XFOIL CL',x:a,y:Q.CL},{name:'points (hover for α, CL, CD, CM)',scatter:true,x:a,y:Q.CL,labels:hoverLabels}];
    if(ref){const lo=ref.attached_range_deg[0],hi=ref.attached_range_deg[1],a0=ref.zero_lift_alpha_deg.value,m=ref.lift_slope_per_deg.value;clSeries.push({name:'Report 824 linear fit',x:[lo,hi],y:[m*(lo-a0),m*(hi-a0)],dash:true});}
    if(gaps.length)clSeries.push(gapSeries);
    // Alpha0 vertical line on CL(alpha) using computed characteristics
    let alpha0Series=[];if(c&&c.zero_lift_alpha_deg!=null)alpha0Series=[{name:'α0 (fitted)',x:[c.zero_lift_alpha_deg,c.zero_lift_alpha_deg],y:[clMin,Math.max(...Q.CL)],dash:true}];
    // Report 824 reference lift line if available
    let refLiftSeries=[];if(ref&&ref.zero_lift_alpha_deg&&ref.lift_slope_per_deg){const a0=ref.zero_lift_alpha_deg.value,m=ref.lift_slope_per_deg.value;refLiftSeries=[{name:'Report 824 lift line (attached-range fit, extrapolated)',x:[a[0],a.at(-1)],y:[m*(a[0]-a0),m*(a.at(-1)-a0)],dash:true}];}
    const clPlotSeries=[...clSeries,...alpha0Series,...refLiftSeries];
    // Drag polar with hover labels and alpha every 5° series
    const fiveDeg=secFiveDegreeLabels(a,Q.CL,Q.CD);
    const cdSeries=[{name:'CD',x:Q.CL,y:Q.CD},{name:'pressure CDp',x:Q.CL,y:Q.CDp,dash:true},{name:'points (hover for α, CL, CD, CM)',scatter:true,x:Q.CL,y:Q.CD,labels:hoverLabels},{name:'α every 5°',scatter:true,x:fiveDeg.x,y:fiveDeg.y,labels:fiveDeg.labels}];
    if(ref)cdSeries.push({name:'Report 824 CDmin',x:[Math.min(...P.CL),Math.max(...P.CL)],y:[ref.cd_min.value,ref.cd_min.value],dash:true});
    // Negative-lift summary
    const negLift=secNegativeLiftSummary(a,Q.CL,gaps);
    let statusText='Negative lift: '+secNegativeLiftText(negLift);
    // Flight alpha range from station 7 launch if available
    let flightRangeSeries=[];if(wb.results.launch&&wb.results.launch.truth&&wb.results.launch.truth.length){const fr=secFlightRange(wb.results.launch);if(fr){flightRangeSeries=[{name:'α range reached in current launch run',x:[fr.min,fr.min],y:[clMin,Math.max(...Q.CL)],dash:true},{name:'α range reached in current launch run',x:[fr.max,fr.max],y:[clMin,Math.max(...Q.CL)],dash:true}];statusText+=` The current station 7 launch run reached α=${fmt(fr.min)}° to ${fmt(fr.max)}° (dashed verticals on CL(α)); check the section data covers it.`;}}const clPlotWithFlight=[...clPlotSeries,...flightRangeSeries];
    out.append(card('Polar',badge(r.evidence_level),...(r.warnings||[]).map(w=>el('div',{class:'wb-warn',text:w})),el('p',{class:'wb-status',id:'wb-sections-gaps',text:gaps.length?`XFOIL did not converge at ${gaps.length} of ${P.requested_alpha_deg.length} angles (${gaps.join(', ')} deg). Those points are gaps, never interpolated.`:`All ${P.requested_alpha_deg.length} requested angles converged.`}),el('p',{class:'wb-status',id:'wb-sections-neglift',text:statusText}),
        el('div',{class:'wb-plots'},
          plotCard({title:'Lift  CL(α)',xlabel:'α (deg)',ylabel:'CL',series:clPlotWithFlight},'wb-plot-cl'),
          plotCard({title:'Drag polar  CD(CL)',xlabel:'CL',ylabel:'CD',series:cdSeries},'wb-plot-cd'),
          plotCard({title:'Pitching moment  CM¼(α)',xlabel:'α (deg)',ylabel:'CM about c/4',series:[{name:'CM',x:a,y:Q.CM},{name:'points (hover for α, CL, CD, CM)',scatter:true,x:a,y:Q.CM,labels:hoverLabels}].concat(ref?[{name:'Report 824',x:ref.attached_range_deg,y:[ref.cm_quarter_chord.value,ref.cm_quarter_chord.value],dash:true}]:[])},'wb-plot-cm'),
          plotCard({title:'Transition location',xlabel:'α (deg)',ylabel:'x/c',series:[{name:'top',x:a,y:Q.Top_Xtr},{name:'bottom',x:a,y:Q.Bot_Xtr}]},'wb-plot-xtr'))),
      card('Characteristics over the attached range',c?table(['quantity','XFOIL'].concat(ref?['Report 824','uncertainty']:[]),[
          ['zero-lift α (deg)',c.zero_lift_alpha_deg,...(ref?[ref.zero_lift_alpha_deg.value,ref.zero_lift_alpha_deg.uncertainty]:[])],
          ['lift slope (per deg)',c.lift_slope_per_deg,...(ref?[ref.lift_slope_per_deg.value,ref.lift_slope_per_deg.uncertainty]:[])],
          ['lift slope (per rad)',c.lift_slope_per_rad,...(ref?[ref.lift_slope_per_deg.value*180/Math.PI,'']:[])],
          ['CM about c/4',c.cm_quarter_chord,...(ref?[ref.cm_quarter_chord.value,ref.cm_quarter_chord.uncertainty]:[])],
          ['CD min',c.cd_min,...(ref?[ref.cd_min.value,ref.cd_min.uncertainty]:[])],
          ['CL max (in sweep)',c.cl_max,...(ref?[ref.cl_max_re3e6?.value,'not gated']:[])]],'wb-sections-characteristics'):el('p',{class:'wb-status',text:'Fewer than three converged points in the attached range.'}),
        el('p',{class:'wb-status',text:'Thin-airfoil hand check: lift slope 2π per rad = 0.1097 per deg; real sections are a few percent lower because of thickness and viscosity.'})),
      r.gates?card('NACA Report 824 comparison (frozen #194 tolerances)',table(['quantity','XFOIL','Report 824','difference','tolerance','result'],Object.entries(r.gates).map(([k,g])=>[k,g.observed,g.expected,g.difference_pct!=null?fmt(g.difference_pct)+' %':g.difference_frac!=null?fmt(g.difference_frac*100)+' %':g.difference,g.tolerance_pct!=null?g.tolerance_pct+' %':g.tolerance_frac!=null?(g.tolerance_frac*100)+' %':g.tolerance,g.pass?'pass':'FAIL']),'wb-sections-gates'),el('p',{class:'wb-status',text:ref.citation})):(r.gates_note?card(null,el('p',{class:'wb-status',text:r.gates_note})):null),
      card('Provenance',table(['item','value'],[['engine',r.provenance.engine_manifest.engine+' '+r.provenance.engine_manifest.version],['binary sha256',r.provenance.engine_manifest.binary_sha256],['input script sha256',r.provenance.input_script_sha256],['polar file sha256',r.provenance.polar_file_sha256]],'wb-sections-provenance'),el('p',{class:'wb-status',text:r.frame})));}
  else out.appendChild(card(null,el('p',{class:'wb-status',text:'Run XFOIL to see the section polar, its attached-range characteristics and the Report 824 comparison.'})));
  return [left,out];
}

// ---------------------------------------------------------------- station 3: aero dataset explorer
async function dsLoad(){try{wb.ds=await api('/datasets');}catch(e){showError('dataset',e);}}
async function dsRun(save=false){if(wb.busy)return;const inputs=wb.inputs.dataset;
  for(const k of ['start','end','step'])inputs.sweep[k]=Number(document.getElementById('wb-in-dataset-sweep-'+k).value);
  const body=clone(inputs),result=save?await saveStation('dataset','dataset',body):await runStation('dataset','dataset',body);
  if(result){wb.results.dataset=save?result.result:result;render();}}
async function dsSelect(){if(wb.busy)return;const body=clone(wb.inputs.dataset.primary);showError('dataset',null);setBusy(true);
  try{await api('/datasets/active',body);await dsLoad();render();}catch(e){showError('dataset',e);}finally{setBusy(false);}}
async function dsImport(){if(wb.busy)return;const body={id:document.getElementById('wb-ds-import-id').value,csv:document.getElementById('wb-ds-import-csv').value};showError('dataset',null);setBusy(true);
  try{await api('/datasets/import',body);await dsLoad();render();}catch(e){showError('dataset',e);}finally{setBusy(false);}}
async function dsRefresh(){if(wb.busy)return;setBusy(true);try{await dsLoad();render();}finally{setBusy(false);}}
async function dsApply(){if(wb.busy)return;showError('dataset',null);
  const provider=clone(wb.inputs.dataset.primary),entry=(wb.ds?.datasets||[]).find(d=>d.id===provider.id);
  if(provider.kind==='dataset'&&!entry?.digest){showError('dataset',new Error('Refresh the dataset list before applying a dataset.'));return;}
  const body={provider,expected_digest:provider.kind==='dataset'?entry.digest:null,definition:null};setBusy(true);
  try{const result=await api('/datasets/snapshot',body);wb.aeroBinding=clone(result);render();}
  catch(e){showError('dataset',e);}finally{setBusy(false);}}
function appliedAeroText(){const b=wb.aeroBinding;return b?.aero_dataset?`Applied to new runs: ${b.identity.id||b.identity.provider_id||'table'}; ${b.identity.evidence_level}; digest ${b.identity.digest}. ${b.scope} Existing runs/studies are unchanged.`:'New runs use the live strip model. Apply a table explicitly in Station 3; existing runs/studies keep their original snapshots.';}
function aeroIdentity(d){return d?el('p',{class:'wb-status wb-aero-identity',text:`Result aero: ${d.id}; ${d.evidence_level}; digest ${d.digest}.${d.load_resolution==='component-resultant'?' Component-resultant loads: root/mount reactions only; distributed plane cuts withheld; elastic forcing unsupported.':''}`}):null;}
function dsOptions(id,current,allowNone){const opts=[];if(allowNone)opts.push(el('option',{value:'',text:'(none)',selected:!current}));
  opts.push(el('option',{value:'strip-model:',text:'strip model (live, estimate)',selected:!!current&&current.kind==='strip-model'}));
  for(const d of (wb.ds?.datasets||[]).filter(d=>d.digest))opts.push(el('option',{value:'dataset:'+d.id,text:`${d.id} (${d.evidence_level})`,selected:!!current&&current.kind==='dataset'&&current.id===d.id}));
  return el('select',{id,onchange:e=>{const v=e.target.value;const ref=v?{kind:v.split(':')[0],id:v.split(':')[1]||null}:null;if(id==='wb-ds-primary')wb.inputs.dataset.primary=ref||{kind:'strip-model',id:null};else wb.inputs.dataset.compare=ref;}},...opts);}
function datasetView(){
  const sid='dataset',inputs=wb.inputs[sid],r=wb.results[sid];
  if(wb.ds===undefined){wb.ds=null;dsLoad().then(render);}
  const act=wb.ds?.active;
  const left=el('div',{},card('Inputs',
      el('label',{class:'wb-field wb-wide'},'Dataset',dsOptions('wb-ds-primary',inputs.primary,false)),
      el('label',{class:'wb-field wb-wide'},'Compare with',dsOptions('wb-ds-compare',inputs.compare,true)),
      el('fieldset',{},el('legend',{text:'Sweep'}),el('label',{class:'wb-field'},'axis',el('select',{id:'wb-in-dataset-sweep-axis',onchange:e=>{inputs.sweep.axis=e.target.value;}},...['alpha_deg','beta_deg','speed_ft_s','left_tail_deg','right_tail_deg','aileron_deg'].map(a=>el('option',{value:a,selected:a===inputs.sweep.axis,text:a})))),
        buildForm({start:inputs.sweep.start,end:inputs.sweep.end,step:inputs.sweep.step},'wb-in-dataset-sweep')),
      el('fieldset',{},el('legend',{text:'Held values'}),buildForm(inputs.fixed,'wb-in-dataset-fixed')),
      actions(sid,[el('button',{class:'wb-btn',id:'wb-run-dataset',onclick:()=>dsRun(false)},'Slice'),
        el('button',{class:'wb-btn wb-secondary',id:'wb-save-dataset',onclick:()=>dsRun(true)},'Slice and save')])),
    card('Datasets',wb.ds?table(['id','evidence','components','alpha range','digest'],wb.ds.datasets.map(d=>[d.id,d.evidence_level||'error',(d.components||[]).join(', '),d.axes?`${d.axes.alpha_deg[0]} … ${d.axes.alpha_deg[1]}`:(d.error||''),(d.digest||'').slice(0,12)]),'wb-ds-list'):el('p',{class:'wb-status',text:'Loading…'}),
      el('p',{class:'wb-status',id:'wb-ds-active',text:act?`Recorded active dataset: ${act.kind==='strip-model'?'strip model':act.id} (digest ${act.digest.slice(0,12)}…, ${act.selected_utc}). Recording is a shelf preference; Apply explicitly to use it in new runs.`:'No active shelf preference recorded. Apply explicitly to configure new runs.'}),
      el('p',{class:'wb-status',id:'wb-ds-applied',text:appliedAeroText()}),
      el('div',{class:'wb-actions'},el('button',{class:'wb-btn',id:'wb-ds-apply',onclick:dsApply},'Apply selected dataset to new runs'),el('button',{class:'wb-btn wb-secondary',id:'wb-ds-select',onclick:dsSelect},'Record selected dataset as active'),
        el('button',{class:'wb-btn wb-secondary',id:'wb-ds-refresh',onclick:dsRefresh},'Refresh dataset list')),
      el('details',{},el('summary',{text:'Import a FlightStream CSV export'}),el('label',{class:'wb-field'},'dataset id',el('input',{type:'text',id:'wb-ds-import-id'})),
        el('textarea',{class:'wb-json',id:'wb-ds-import-csv',spellcheck:'false',placeholder:'# reference_area_in2: …  (layout in docs/mechanics/AERO_DATASET.md)'}),
        el('div',{class:'wb-actions'},el('button',{class:'wb-btn wb-secondary',id:'wb-ds-import',onclick:dsImport},'Import'))),
      card('Saved runs',el('div',{id:'wb-saved-'+sid}))));
  const out=el('div',{id:'wb-result-dataset'});
  if(r){const sets=[['primary',r.primary]].concat(r.compare?[['compare',r.compare]]:[]);const x=r.values;const name=(k,d)=>`${d.provider.id}${k==='compare'?' (compare)':''}`;
    const line=(key,f)=>sets.map(([k,d],i)=>({name:name(k,d),x,y:d.rows.map(f),dash:i>0}));
    const gaps=sets.flatMap(([k,d])=>d.rows.filter(row=>!row.in_coverage).map(row=>({k,row})));
    const covered=r.primary.rows.find(row=>Object.keys(row.components).length);const comp=covered?Object.keys(covered.components):[];
    out.append(card('Slice',...sets.map(([k,d])=>badge(d.provider.evidence_level)),el('p',{class:'wb-status',text:`${r.frame}. Coefficients use S = ${fmt(r.reference.area_in2)} in² and c = ${fmt(r.reference.chord_in)} in.`}),
        gaps.length?el('div',{class:'wb-warn',id:'wb-ds-coverage',text:`${gaps.length} slice points are outside a dataset's coverage and return no load (never extrapolated): ${gaps.slice(0,4).map(g=>g.k+' at '+g.row.value+': '+g.row.violations.join('; ')).join(' | ')}${gaps.length>4?' …':''}`}):el('p',{class:'wb-status',id:'wb-ds-coverage',text:'Every slice point is inside coverage.'}),
        el('div',{class:'wb-plots'},
          plotCard({title:'Lift coefficient',xlabel:r.axis,ylabel:'CL',series:line('CL',row=>row.in_coverage?row.CL:null)},'wb-ds-plot-cl'),
          plotCard({title:'Drag coefficient',xlabel:r.axis,ylabel:'CD',series:line('CD',row=>row.in_coverage?row.CD:null)},'wb-ds-plot-cd'),
          plotCard({title:'Pitching moment about the CG',xlabel:r.axis,ylabel:'Cm',series:line('Cm',row=>row.in_coverage?row.Cm:null)},'wb-ds-plot-cm'),
          plotCard({title:'Component normal force (primary)',xlabel:r.axis,ylabel:'Fz body (lbf)',series:comp.map(c=>({name:c,x,y:r.primary.rows.map(row=>row.components[c]?.force_B_lbf[2])}))},'wb-ds-plot-components'))),
      card('Points (primary)',table([r.axis,'Fx','Fy','Fz','Mx','My','Mz','lift (lbf)','drag (lbf)','coverage','evidence'],r.primary.rows.map(row=>[row.value,...row.force_B_lbf,...row.moment_B_in_lbf,row.lift_lbf,row.drag_lbf,row.in_coverage?'in':'OUT',row.evidence_level]),'wb-ds-points')));}
  else out.appendChild(card(null,el('p',{class:'wb-status',text:'Slice a dataset to see coefficients, component split, coverage and evidence.'})));
  return [left,out];
}

// ---------------------------------------------------------------- station 2: AVL (same adapter as production estimates)
function aero3dView(){
  const sid='aero-3d',inputs=wb.inputs[sid],r=wb.results[sid];
  const textInput=(key,label)=>el('label',{class:'wb-field'},label,el('input',{id:'wb-in-'+sid+'-'+key,value:inputs[key],oninput:e=>{inputs[key]=e.target.value;}}));
  async function execute(save){if(wb.busy)return;let body;try{body=clone(inputs);body.definition=JSON.parse(document.getElementById('wb-aero-3d-definition').value);wb.inputs[sid]=clone(body);}catch(e){showError(sid,'Aircraft JSON: '+e.message);return;}
    const result=save?await saveStation(sid,sid,body):await runStation(sid,sid,body);if(result){wb.results[sid]=save?result.result:result;render();}}
  const left=el('div',{},card('Inputs',el('label',{},'Aircraft definition (JSON; geometry in inches)',el('textarea',{class:'wb-json',id:'wb-aero-3d-definition',spellcheck:'false'},JSON.stringify(inputs.definition,null,1))),
    textInput('wing_section','Wing NACA digits'),textInput('tail_section','Tail NACA digits'),buildForm(inputs.case,'wb-in-aero-3d-case'),
    el('label',{class:'wb-field'},'Chordwise mesh',numberInput('wb-in-aero-3d-n_chord',inputs.n_chord,x=>{inputs.n_chord=x;},false)),
    el('label',{class:'wb-field'},'Spanwise mesh',numberInput('wb-in-aero-3d-n_span',inputs.n_span,x=>{inputs.n_span=x;},false)),
    el('label',{class:'wb-field'},'Compare doubled mesh',el('input',{type:'checkbox',id:'wb-in-aero-3d-refine',checked:inputs.refine,onchange:e=>{inputs.refine=e.target.checked;}})),
    actions(sid,[el('button',{class:'wb-btn',id:'wb-run-aero-3d',onclick:()=>execute(false)},'Run AVL'),el('button',{class:'wb-btn wb-secondary',id:'wb-save-aero-3d',onclick:()=>execute(true)},'Run and save')])),card('Saved runs',el('div',{id:'wb-saved-aero-3d'})));
  const out=el('div',{id:'wb-result-aero-3d'});
  if(r){const c=r.coarse,tot=c.total_forces[0],strips=c.strip_forces[0],groups=[...new Set(strips.map(s=>s.name))];
    out.append(card('AVL coefficients',badge(r.evidence_level),el('p',{text:r.frame}),el('p',{class:'wb-warn',text:r.warning}),table(['coefficient','value'],['CL','CD','CY','Cl','Cm','Cn'].map(k=>[k,tot[k]]),'wb-aero-3d-coefficients')));
    for(const [key,title,units,id] of [['c_cl_ft','Span loading','chord × cl (ft)','wb-aero-3d-span'],['far_wake_downwash_over_v','Far-wake downwash / freestream','dimensionless (not angle)','wb-aero-3d-wake']])out.append(plotCard({title,xlabel:'body y at strip LE (ft)',ylabel:units,series:groups.map(name=>{const rows=strips.filter(s=>s.name===name);return {name,x:rows.map(s=>s.y_le_ft),y:rows.map(s=>s[key])};})},id));
    out.append(card('Stability-axis derivatives',el('p',{text:'Alpha/beta: per radian. Control d1=aileron, d2=left tail, d3=right tail: per degree. p/q/r derivatives use dimensionless stability rates.'}),table(['AVL derivative','value'],Object.entries(c.derivatives[0]),'wb-aero-3d-derivatives')));
    out.append(card('Tail flow-survey diagnostics',table(['panel','downwash (rad)','position geometry (ft)','velocity / V geometry'],Object.entries(c.downwash_at_tail[0]).map(([name,v])=>[name,v.downwash_rad,JSON.stringify(v.position_geometry_ft),JSON.stringify(v.velocity_over_v_geometry)]),'wb-aero-3d-tail')));
    out.append(card('Mesh refinement',r.refinement?table(['coefficient','coarse','refined','absolute change','relative change','0.5% gate'],Object.entries(r.refinement.checks).map(([k,v])=>[k,v.coarse,v.refined,v.absolute_change,v.relative_change,v.relative_change===null?'unavailable':v.passed?'pass':'FAIL']),'wb-aero-3d-refinement'):el('p',{text:'Not requested.'})));
    out.append(card('Idealized induced-angle hand check',el('p',{text:r.induced_handcheck.scope}),table(['quantity','value'],Object.entries(r.induced_handcheck).filter(([k])=>k!=='scope'),'wb-aero-3d-handcheck')));
    out.append(card('References and provenance',el('pre',{id:'wb-aero-3d-provenance',text:JSON.stringify({reference:c.reference,discretization:c.discretization,input_sha256:c.input_sha256,definition_sha256:c.definition_sha256,engine_manifest:c.engine_manifest},null,2)}),el('details',{},el('summary',{text:'Complete adapter results (including both meshes and raw output)'}),el('pre',{text:JSON.stringify(r,null,2)}))));
  }else out.append(card(null,el('p',{text:'Run AVL for an attached-flow estimate, span loading and numerical diagnostics.'})));
  return [left,out];
}

// ---------------------------------------------------------------- station 4: atmosphere & gusts
function environmentView(){
  const sid='environment',inputs=wb.inputs[sid],r=wb.results[sid];
  const left=card('Inputs',buildForm(inputs,'wb-in-'+sid),actions(sid,[
    el('button',{class:'wb-btn',id:'wb-run-'+sid,onclick:async()=>{const x=await runStation(sid,'environment',inputs);if(x){wb.results[sid]=x;render();}}},'Run station'),
    el('button',{class:'wb-btn wb-secondary',id:'wb-save-'+sid,onclick:async()=>{const rec=await saveStation(sid,'environment',inputs);if(rec){wb.results[sid]=rec.result;render();}}},'Run and save')]),
    card('Saved runs',el('div',{id:'wb-saved-'+sid})));
  const out=el('div',{id:'wb-result-'+sid});
  if(r){const a=r.atmosphere,t=r.turbulence,g=r.discrete;
    out.append(
      card('ISA 1976 atmosphere above the field',table(['height AGL (ft)','T (degR)','p (lbf/ft²)','ρ (slug/ft³)','σ = ρ/ρ0','a (ft/s)','μ (slug/ft·s)','density alt (ft)'],a.table.map(x=>[x.height_agl_ft,x.temperature_R,x.pressure_lbf_ft2,x.density_slug_ft3,x.density_ratio,x.sound_speed_ft_s,x.viscosity_slug_ft_s,x.density_altitude_ft]),'wb-isa-table'),
        el('p',{class:'wb-status',text:`Hand check, sea level standard: T = ${fmt(a.hand_check.temperature_R)} degR, p = ${fmt(a.hand_check.pressure_lbf_ft2)} lbf/ft², ρ = ${fmt(a.hand_check.density_slug_ft3)} slug/ft³ (textbook 518.67, 2116.22, 0.0023769).`})),
      el('div',{class:'wb-plots'},
        plotCard({title:'Discrete 1-cos gust  U(s) = (U/2)(1 − cos(πs/H))',xlabel:'time at airspeed (s)',ylabel:'gust velocity (ft/s)',series:[{name:'U(t)',x:g.time_s,y:g.velocity_ft_s}]},'wb-plot-gust'),
        plotCard({title:'Dryden turbulence realization',xlabel:'time (s)',ylabel:'velocity (ft/s)',series:['u','v','w'].map(k=>({name:k,x:t.time_s,y:t.velocity_ft_s[k]}))},'wb-plot-turbulence'),
        ...Object.entries(t.psd).map(([k,p])=>plotCard({title:`PSD ${k}: continuous Dryden vs exact discrete`,xlabel:'ω (rad/s)',ylabel:'Φ ((ft/s)²/(rad/s))',logx:true,logy:true,series:[{name:'continuous',x:t.omega_rad_s,y:p.continuous},{name:'discrete (exact)',x:t.omega_rad_s,y:p.discrete,dash:true}]},'wb-plot-psd-'+k))),
      card('Turbulence statistics',table(['component','target σ (ft/s)','sample σ (ft/s)'],['u','v','w'].map(k=>[k,t.target_std_ft_s[k],t.sample_std_ft_s[k]]),'wb-turb-stats'),el('p',{class:'wb-status',text:t.frame+'. Sample σ of a finite record scatters about the target; the PSD overlay is the exact check.'})));}
  else out.appendChild(card(null,el('p',{class:'wb-status',text:'Run the station to see the atmosphere table, gust profile and turbulence.'})));
  return [left,out];
}

// ---------------------------------------------------------------- station 5: propulsion
function propulsionView(){
  const sid='propulsion',inputs=wb.inputs[sid],r=wb.results[sid];
  const left=card('Inputs',buildForm(inputs,'wb-in-'+sid),actions(sid,[
    el('button',{class:'wb-btn',id:'wb-run-'+sid,onclick:async()=>{const x=await runStation(sid,'propulsion',inputs);if(x){wb.results[sid]=x;render();}}},'Run station'),
    el('button',{class:'wb-btn wb-secondary',id:'wb-save-'+sid,onclick:async()=>{const rec=await saveStation(sid,'propulsion',inputs);if(rec){wb.results[sid]=rec.result;render();}}},'Run and save')]),
    card('Saved runs',el('div',{id:'wb-saved-'+sid})));
  const out=el('div',{id:'wb-result-'+sid});
  if(r){const T=r.table,x=T.map(v=>v.axial_speed_ft_s);
    out.append(el('div',{class:'wb-plots'},plotCard({title:'Thrust available  T_max(V) = min(T_s(1 − V/Vp), Pη/V)',xlabel:'axial airspeed (ft/s)',ylabel:'thrust (lbf)',series:[
        {name:'linear bound',x,y:T.map(v=>v.linear_bound_lbf),dash:true},{name:'power bound',x,y:T.map(v=>v.power_bound_lbf),dash:true},{name:'available',x,y:T.map(v=>v.available_lbf)},{name:'delivered',x,y:T.map(v=>v.delivered_lbf)}]},'wb-plot-thrust'),
      plotCard({title:'Shaft reaction torque  Q = sign·k_Q·T',xlabel:'axial airspeed (ft/s)',ylabel:'torque about body +x (in-lbf)',series:[{name:'Q',x,y:T.map(v=>v.shaft_torque_in_lbf)}]},'wb-plot-torque')),
      card('Summary',badge(r.evidence_level),el('p',{text:`Crossover speed (power bound starts to limit): ${r.crossover_speed_ft_s==null?'none below pitch speed':fmt(r.crossover_speed_ft_s)+' ft/s'}. Hand check: available at V = 0 is ${fmt(r.hand_check.static_available_lbf)} lbf = static thrust ${fmt(r.hand_check.static_thrust_lbf)} lbf.`}),el('p',{class:'wb-status',text:r.frame})),
      card('Table',table(['V (ft/s)','linear (lbf)','power (lbf)','available (lbf)','delivered (lbf)','torque (in-lbf)','saturated'],T.map(v=>[v.axial_speed_ft_s,v.linear_bound_lbf,v.power_bound_lbf,v.available_lbf,v.delivered_lbf,v.shaft_torque_in_lbf,v.saturated?'yes':'no']),'wb-prop-table')),
      card('Parameter basis',table(['parameter','value','range','unit','basis'],Object.entries(r.parameters).map(([k,p])=>[k,r.propulsion[k],p.low+' – '+p.high,p.unit,p.basis]))));}
  else out.appendChild(card(null,el('p',{class:'wb-status',text:'Run the station to see thrust available versus airspeed.'})));
  return [left,out];
}

// ---------------------------------------------------------------- cursor playback (#267)
// Playback keeps its own clock in recorded seconds and shows the recorded tick at or after
// that time: no interpolation. Each frame waits for its evaluation to finish, so slow
// evaluations skip ticks rather than showing a result that belongs to another tick.
function playbackIndexAt(times,t){const n=times.length;if(!n)return -1;let i=0;while(i<n-1&&times[i]<t-1e-12)i++;return i;}
function playbackButton(key,playing){const b=document.getElementById('wb-'+key+'-play');if(b){b.textContent=playing?'Pause':'Play';b.setAttribute('aria-pressed',String(playing));}}
function stopPlayback(key){const p=wb.players?.[key];if(!p)return;if(p.timer)clearTimeout(p.timer);p.playing=false;p.timer=null;p.token=(p.token||0)+1;playbackButton(key,false);}
function stopAllPlayback(){for(const key of Object.keys(wb.players||{}))stopPlayback(key);}
function playbackReducedMotion(){try{return typeof matchMedia==='function'&&matchMedia('(prefers-reduced-motion: reduce)').matches;}catch{return false;}}
function startPlayback(key,getRun,getIndex,setIndex){
  const p=wb.players[key],run=getRun();if(!run?.truth?.length)return;
  const times=run.truth.map(r=>r.time_s),token=p.token=(p.token||0)+1;
  if(getIndex()>=times.length-1)setIndex(0,false);
  p.playing=true;p.clock=times[getIndex()];p.wall=Date.now();playbackButton(key,true);
  const alive=()=>p.playing&&p.token===token&&getRun()===run;
  const frame=async()=>{
    if(!alive())return;
    const now=Date.now();p.clock+=p.speed*(now-p.wall)/1000;p.wall=now;
    const last=times[times.length-1];
    if(p.clock>last+1e-12){if(p.loop){p.clock=times[0];}else if(getIndex()===times.length-1){stopPlayback(key);return;}}
    const index=Math.min(playbackIndexAt(times,p.clock),times.length-1);
    if(index!==getIndex())await setIndex(index,true);
    if(alive())p.timer=setTimeout(frame,50);};
  p.timer=setTimeout(frame,50);
}
function cursorPlayer(key,getRun,getIndex,setIndex){
  wb.players??={};
  wb.players[key]??={playing:false,speed:playbackReducedMotion()?0.25:1,loop:false,timer:null,token:0};
  const p=wb.players[key];
  const play=el('button',{class:'wb-btn wb-secondary',id:'wb-'+key+'-play','aria-pressed':String(p.playing),
    onclick:()=>p.playing?stopPlayback(key):startPlayback(key,getRun,getIndex,setIndex)},p.playing?'Pause':'Play');
  const speed=el('select',{id:'wb-'+key+'-speed','aria-label':'playback speed',onchange:e=>{p.speed=Number(e.target.value);}},
    ...[0.25,1,5].map(v=>el('option',{value:String(v),selected:v===p.speed,text:v+'× recorded time'})));
  const loop=el('label',{class:'wb-player-loop'},el('input',{type:'checkbox',id:'wb-'+key+'-loop',checked:p.loop,onchange:e=>{p.loop=e.target.checked;}}),' loop');
  return el('div',{class:'wb-player',id:'wb-'+key+'-player'},play,speed,loop,
    el('span',{class:'wb-status',text:'Steps through recorded instants only; slow evaluations skip instants rather than blend them.'}));
}

// ---------------------------------------------------------------- station 6: 6DOF plant
function upstream(){const env=wb.inputs.environment.atmosphere;const result={definition:null,
  atmosphere:wb.options.atmosphere?{model:'isa',field_elevation_ft:env.field_elevation_ft,temperature_offset_R:env.temperature_offset_R}:null,
  propulsion:wb.options.propulsion?clone(wb.inputs.propulsion.propulsion):null};if(wb.aeroBinding?.aero_dataset)result.aero_dataset=clone(wb.aeroBinding.aero_dataset);return result;}
function snapshotUpstream(inputs){const result={definition:clone(inputs.definition),atmosphere:clone(inputs.atmosphere),propulsion:clone(inputs.propulsion)};if(inputs.aero_dataset)result.aero_dataset=clone(inputs.aero_dataset);return result;}
function upstreamOptions(){const sid=wb.current;return el('fieldset',{},el('legend',{text:'Upstream stations'}),
    el('label',{class:'wb-field'},'ISA atmosphere from station 4 (field elevation, ΔT)',el('input',{type:'checkbox',id:'wb-'+sid+'-use-atmosphere',checked:wb.options.atmosphere,onchange:e=>{wb.options.atmosphere=e.target.checked;}})),
    el('label',{class:'wb-field'},'Propeller map from station 5',el('input',{type:'checkbox',id:'wb-'+sid+'-use-propulsion',checked:wb.options.propulsion,onchange:e=>{wb.options.propulsion=e.target.checked;}})),
    el('p',{class:'wb-status',text:'Aircraft: stable example (examples/aircraft-stable.json). Unchecked: uniform sea-level air and ideal thrust (the field-case defaults).'}),el('p',{class:'wb-status wb-aero-identity',text:appliedAeroText()}));}
function freezeBody(){const p=wb.inputs.plant;return {...upstream(),state:clone(p.state),state_SI:null,time_s:null,controls:clone(p.controls)};}
function runBody(){const p=wb.inputs.plant;return {...upstream(),controls:clone(p.controls),run:clone(p.run)};}
function vec(v,d=3){return v.map(x=>fmt(x,d));}
function componentsView(r,id){
  if(r.status!=='ok')return el('div',{class:'wb-error',id,text:r.status+': '+(r.error||'')});
  const rows=Object.entries(r.components).map(([c,g])=>[c,g.elements,...g.force_B_lbf,...g.moment_about_CG_B_in_lbf]);
  rows.push(['total (non-gravity)','',...r.totals.non_gravity_force_B_lbf,...r.totals.moment_about_CG_B_in_lbf]);
  rows.push(['gravity','',...r.totals.gravity_B_lbf,0,0,0]);
  return el('div',{id},badge(r.evidence_level),aeroIdentity(r.aero_dataset),
    table(['component','elements','Fx (lbf)','Fy (lbf)','Fz (lbf)','Mx (in-lbf)','My (in-lbf)','Mz (in-lbf)'],rows,id+'-table'),
    el('p',{class:'wb-status',text:r.frame+'. Gravity acts at the CG, so it has no moment about the CG.'}),
    table(['quantity','x','y','z'],[['air velocity, body (ft/s)',...r.velocity_air_B_ft_s],['body rates (deg/s)',...r.omega_B_deg_s],['load factor n = F/W (aero + thrust)',...r.totals.load_factor_B],['CG acceleration dv/dt, body (ft/s²)',...r.derivative.vdot_B_ft_s2],['angular acceleration (deg/s²)',...r.derivative.omegadot_B_deg_s2],['CG position from nose datum (in)',...r.cg_B_in]],id+'-kinematics'),
    el('p',{class:'wb-status',text:`Hand check: component force sum = [${vec(r.hand_check.component_force_sum_B_lbf).join(', ')}] lbf equals the total; |gravity| = ${fmt(r.hand_check.gravity_magnitude_lbf)} lbf = weight ${fmt(r.weight_lbf)} lbf. Air density ${fmt(r.environment.density_slug_ft3)} slug/ft³${r.environment.source?' ('+r.environment.source+')':''}.`}),
    r.propulsion?el('p',{class:'wb-status',text:`Propeller map: commanded ${fmt(r.propulsion.commanded_thrust_lbf)} lbf, available ${fmt(r.propulsion.available_thrust_lbf)} lbf, delivered ${fmt(r.propulsion.delivered_thrust_lbf)} lbf${r.propulsion.saturated?' (saturated)':''}.`}):null,
    el('details',{},el('summary',{text:`Element ledger (${r.ledger.length} rows)`}),table(['element','component','x (in)','y (in)','z (in)','Fx','Fy','Fz','My about CG','local α (deg)'],r.ledger.map(l=>[l.name,l.component,...l.position_B_in,...l.force_B_lbf,l.moment_about_CG_B_in_lbf[1],l.alpha_deg]),id+'-ledger')));
}
function setPlantRun(run,inputs){clearTimeout(wb.freezeTimer);stopPlayback('plant');wb.plantRun=run;wb.plantRunSnapshot=clone(inputs);wb.cursor=0;wb.results.plantCursor=null;wb.plantCursorRequest=(wb.plantCursorRequest||0)+1;}
function setPlantCursor(i,immediate){const run=wb.plantRun;if(!run)return;const n=run.truth.length;wb.cursor=Math.max(0,Math.min(n-1,Math.round(Number(i))||0));const rr=run.truth[wb.cursor];
  const lab=document.getElementById('wb-cursor-label');if(lab)lab.textContent=`t = ${fmt(rr.time_s)} s, height ${fmt(rr.height_agl_ft)} ft, airspeed ${fmt(rr.airspeed_ft_s)} ft/s, α ${fmt(rr.alpha_deg)} deg`;
  const sl=document.getElementById('wb-plant-slider');if(sl)sl.value=String(wb.cursor);clearTimeout(wb.freezeTimer);if(immediate)return freezeAtCursor();wb.freezeTimer=setTimeout(freezeAtCursor,120);}
async function freezeAtCursor(){const run=wb.plantRun,snapshot=wb.plantRunSnapshot,cursor=wb.cursor;if(!run||!snapshot||!run.truth[cursor])return;
  const row=run.truth[cursor],request=wb.plantCursorRequest=(wb.plantCursorRequest||0)+1;
  const current=()=>wb.plantRun===run&&wb.cursor===cursor&&wb.plantCursorRequest===request;
  try{const r=await api('/run/plant',{...snapshotUpstream(snapshot),state:null,state_SI:clone(row.state_SI),time_s:row.time_s,controls:clone(run.controls)});if(!current())return;wb.results.plantCursor=r;const box=document.getElementById('wb-cursor-components');if(box)box.replaceWith(componentsView(r,'wb-cursor-components'));drawRunPlots();}
  catch(e){if(current())showError('plant',e);}}
function drawRunPlots(){const run=wb.plantRun,box=document.getElementById('wb-run-plots');if(!run||!box)return;const T=run.truth,t=T.map(r=>r.time_s),cx=T[wb.cursor]?.time_s;box.textContent='';
  const specs=[['height AGL (ft)',[['height',T.map(r=>r.height_agl_ft)]]],['airspeed (ft/s)',[['airspeed',T.map(r=>r.airspeed_ft_s)]]],['angle (deg)',[['alpha',T.map(r=>r.alpha_deg)],['beta',T.map(r=>r.beta_deg)],['pitch θ',T.map(r=>r.theta_deg)],['flight path',T.map(r=>r.flight_path_deg)]]],['body rate (deg/s)',['p','q','r'].map((n,i)=>[n,T.map(r=>r.omega_B_deg_s[i])])]];
  for(const [label,series] of specs)box.appendChild(plotCard({xlabel:'time from tube exit (s)',ylabel:label,cursorX:cx,height:210,series:series.map(([name,y])=>({name,x:t,y}))},'wb-plot-run-'+label.split(' ')[0]));}
function linearReferenceCard(){
  wb.linearInputs??={definition:null,airspeed_ft_s:200,field_case:null,include_avl:true};
  wb.linearRaw??={definition:'null',field_case:'null'};
  const i=wb.linearInputs,r=wb.linearReport,box=card('Independent trim and linear-reference experiment',
    el('p',{text:'Independent strip-model reference, not the applied table or live flight cursor. Defaults: illustrative stable aircraft, uniform atmosphere, zero wind and fixed thrust. Same-model verification only; legacy longitudinal, four-panel and AVL use different aerodynamic assumptions.'}),
    el('label',{class:'wb-field'},'Trim airspeed (ft/s)',numberInput('wb-linear-speed',i.airspeed_ft_s,x=>{i.airspeed_ft_s=x;},false)),
    el('label',{class:'wb-field'},'Include separate AVL reference',el('input',{type:'checkbox',id:'wb-linear-avl',checked:i.include_avl,onchange:e=>{i.include_avl=e.target.checked;}})),
    ...['definition','field_case'].map(k=>el('details',{},el('summary',{text:k+' JSON (null selects defaults)'}),el('textarea',{class:'wb-json',id:'wb-linear-'+k,oninput:e=>{wb.linearRaw[k]=e.target.value;}},wb.linearRaw[k]))),
    el('button',{class:'wb-btn',id:'wb-run-linear-check',onclick:async()=>{if(wb.busy)return;let body;try{for(const k of ['definition','field_case'])i[k]=JSON.parse(wb.linearRaw[k]);body=clone(i);}catch(e){showError('linear-check',e);return;}const result=await runStation('linear-check','linear-check',body);if(result){wb.linearReport=result;render();}}},'Run trim / linear cross-check'),
    el('div',{id:'wb-status-linear-check',class:'wb-status'}),el('div',{id:'wb-error-linear-check',class:'wb-error',role:'alert'}),
    el('p',{},el('a',{id:'wb-linear-legacy',href:'/aircraft#dynamic-panel'},'Legacy longitudinal learning view'),' · ',el('a',{id:'wb-linear-four-panel',href:'/aircraft#release-panel'},'Four-panel lateral/release learning view')));
  if(r)box.append(el('p',{id:'wb-linear-verdict',text:r.passed?'Same-model numerical verification passed (5% frequency; <1% refinement). Not aircraft validation.':'Same-model numerical verification FAILED.'}),
    el('p',{text:'Retained report input: '+fmt(r.inputs.bench.target.airspeed_ft_s)+' ft/s; editing controls above affects the next experiment.'}),
    table(['mode','analytic frequency (rad/s)','production frequency (rad/s)','frequency error (%)','step error (%)','gate'],r.modes.map(m=>[m.name,m.analytic_frequency_rad_s,m.numeric_frequency_rad_s,100*m.frequency_relative_error,100*m.refinement_relative_error,m.passed?'pass':'FAIL']),'wb-linear-modes'),
    table(['separate reference','availability'],Object.entries(r.reference_models).map(([k,v])=>[k,v.status]),'wb-linear-references'),
    el('details',{},el('summary',{text:'Matrices, exact inputs, source identities and separate reference results'}),el('pre',{id:'wb-linear-report',text:JSON.stringify(r,null,2)})));
  return box;
}
function plantView(){
  const sid='plant',p=wb.inputs.plant,r=wb.results.plant;
  const opts=upstreamOptions();
  const left=el('div',{},card('Inputs',opts,
      el('fieldset',{},el('legend',{text:'Freeze at a flight state'}),buildForm(p.state,'wb-in-plant-state')),
      el('fieldset',{},el('legend',{text:'Controls (held fixed)'}),buildForm(p.controls,'wb-in-plant-controls')),
      actions(sid,[el('button',{class:'wb-btn',id:'wb-plant-freeze',onclick:async()=>{const x=await runStation(sid,'plant',freezeBody());if(x){wb.results.plant=x;render();}}},'Freeze plant at state'),
        el('button',{class:'wb-btn wb-secondary',id:'wb-save-plant',onclick:async()=>{const rec=await saveStation(sid,'plant',freezeBody());if(rec){wb.results.plant=rec.result;render();}}},'Freeze and save')])),
    card('Free flight from tube exit',el('p',{class:'wb-status',text:'Fixed controls from above; run_launch with the same plant. Step the cursor to freeze the plant at any recorded instant.'}),
      buildForm(p.run,'wb-in-plant-run'),
      el('div',{class:'wb-actions'},el('button',{class:'wb-btn',id:'wb-plant-run',onclick:async()=>{const body=clone(runBody());const x=await runStation(sid,'plant-run',body);if(x){setPlantRun(x,body);render();freezeAtCursor();}}},'Run free flight'),
        el('button',{class:'wb-btn wb-secondary',id:'wb-plant-run-save',onclick:async()=>{const body=clone(runBody());const rec=await saveStation(sid,'plant-run',body);if(rec){setPlantRun(rec.result,rec.inputs);render();freezeAtCursor();}}},'Run and save'))),
    card('Saved runs',el('div',{id:'wb-saved-'+sid})));
  const out=el('div',{id:'wb-result-plant'});
  out.appendChild(el('div',{class:'wb-warn',id:'wb-plant-caveat',text:'Different aero models retain the #199 F4 discrepancy: production strip short period ~5.16 rad/s vs legacy ~3.82 at200ft/s. The independent same-strip-model check below tests implementation agreement, not physical validation. Dynamic results remain estimates.'}));
  out.appendChild(card('Frozen state: forces and moments by component',r?componentsView(r,'wb-components'):el('p',{class:'wb-status',text:'Freeze the plant to see every force and moment by component.'})));
  const run=wb.plantRun;
  if(run){const n=run.truth.length;const row=run.truth[Math.min(wb.cursor,n-1)];
    const player=cursorPlayer('plant',()=>wb.plantRun,()=>wb.cursor,setPlantCursor);
    const slider=el('input',{type:'range',id:'wb-plant-slider',min:'0',max:String(n-1),step:'1',value:String(wb.cursor),oninput:e=>{stopPlayback('plant');setPlantCursor(e.target.value);}});
    out.appendChild(card('Free-flight run',badge(run.evidence_level),el('p',{class:'wb-status',id:'wb-run-status',text:`Status ${run.status}${run.stop_reason?' — '+run.stop_reason:''}; ${n} recorded instants, dt ${run.dt_s} s.`}),
      el('p',{class:'wb-status',text:"Cursor loads use this run's saved input snapshot. Editing upstream settings affects new runs only."}),
      el('div',{class:'wb-plots',id:'wb-run-plots'}),player,slider,el('div',{class:'wb-status',id:'wb-cursor-label',text:`t = ${fmt(row.time_s)} s, height ${fmt(row.height_agl_ft)} ft, airspeed ${fmt(row.airspeed_ft_s)} ft/s, α ${fmt(row.alpha_deg)} deg`}),
      wb.results.plantCursor?componentsView(wb.results.plantCursor,'wb-cursor-components'):el('div',{id:'wb-cursor-components',class:'wb-status',text:'Evaluating…'})));}
  out.appendChild(linearReferenceCard());
  return [left,out];
}

// ---------------------------------------------------------------- station 7: launch
function exitSideProjection(v,headingDeg){const h=headingDeg*Math.PI/180,n=Math.hypot(...v)||1;return [(v[0]*Math.cos(h)+v[1]*Math.sin(h))/n,-v[2]/n];}
function exitDiagram(ex,headingDeg){
  // Side view in the vertical plane of the tube: horizontal = along heading (ft), vertical = up.
  const W=300,H=220,o={x:60,y:170},L=110,svg=svgEl('svg',{viewBox:`0 0 ${W} ${H}`,class:'wb-plot',id:'wb-exit-diagram',role:'img','aria-label':'tube exit geometry, side view'});
  const arrow=(v,cls,label)=>{const [along,up]=exitSideProjection(v,headingDeg),x=o.x+L*along,y=o.y-L*up,k=svg.querySelectorAll('.wb-key').length;svgEl('line',{x1:o.x,y1:o.y,x2:x,y2:y,class:'wb-series '+cls,'stroke-dasharray':k?(k===1?'8 4':'2 3'):'none'},svg);const ly=36+k*16;svgEl('line',{x1:W-110,x2:W-90,y1:ly-4,y2:ly-4,class:'wb-series wb-key '+cls,'stroke-dasharray':k?(k===1?'8 4':'2 3'):'none'},svg);svgEl('text',{x:W-86,y:ly,class:'wb-tick'},svg).textContent=label;};
  svgEl('line',{x1:10,y1:o.y,x2:W-10,y2:o.y,class:'wb-frame'},svg);svgEl('text',{x:W-80,y:o.y+14,class:'wb-tick'},svg).textContent='horizontal →';
  arrow(ex.tube_axis_N,'wb-s0','tube axis');arrow(ex.body_x_axis_N,'wb-s1','body x');arrow(ex.velocity_N_ft_s,'wb-s2','velocity');
  svgEl('text',{x:10,y:14,class:'wb-plot-title'},svg).textContent='Exit geometry (side view)';return svg;}
function launchBody(){const l=wb.inputs.launch;return {...upstream(),launch:clone(l.launch),controls:clone(l.controls),duration_s:l.duration_s,dt_s:l.dt_s};}
function setLaunchRun(result,inputs){clearTimeout(wb.freezeTimer);stopPlayback('loads');wb.results.launch=result;wb.launchUsed=clone(inputs);wb.loadsCursor=0;wb.results.loads=null;wb.savedLoadsRecord=null;wb.loadsRequestToken=(wb.loadsRequestToken||0)+1;}
function setLoadsCursor(i,immediate){const run=wb.results.launch;if(!run||wb.savedLoadsRecord)return;const n=run.truth.length;wb.loadsCursor=Math.max(0,Math.min(n-1,Math.round(Number(i))||0));const rr=run.truth[wb.loadsCursor];
  const lab=document.getElementById('wb-loads-cursor-label');if(lab)lab.textContent=`t = ${fmt(rr.time_s)} s, height ${fmt(rr.height_agl_ft)} ft, airspeed ${fmt(rr.airspeed_ft_s)} ft/s, α ${fmt(rr.alpha_deg)} deg`;
  const sl=document.getElementById('wb-loads-slider');if(sl)sl.value=String(wb.loadsCursor);clearTimeout(wb.freezeTimer);if(immediate)return evaluateLoads();wb.freezeTimer=setTimeout(evaluateLoads,120);}
function launchView(){
  const sid='launch',l=wb.inputs.launch,r=wb.results.launch;
  const left=el('div',{},card('Inputs',upstreamOptions(),
      el('fieldset',{},el('legend',{text:'Launch scenario'}),buildForm(l.launch,'wb-in-launch-launch')),
      el('fieldset',{},el('legend',{text:'Controls (held fixed from exit)'}),buildForm(l.controls,'wb-in-launch-controls')),
      buildForm({duration_s:l.duration_s,dt_s:l.dt_s},'wb-in-launch-run'),
      actions(sid,[el('button',{class:'wb-btn',id:'wb-run-launch',onclick:async()=>{syncRun();const body=clone(launchBody());const x=await runStation(sid,'launch',body);if(x){setLaunchRun(x,body);render();}}},'Run launch'),
        el('button',{class:'wb-btn wb-secondary',id:'wb-save-launch',onclick:async()=>{syncRun();const body=clone(launchBody());const rec=await saveStation(sid,'launch',body);if(rec){setLaunchRun(rec.result,rec.inputs);render();}}},'Run and save')])),
    card('Saved runs',el('div',{id:'wb-saved-'+sid})));
  function syncRun(){l.duration_s=Number(document.getElementById('wb-in-launch-run-duration_s').value);l.dt_s=Number(document.getElementById('wb-in-launch-run-dt_s').value);}
  const out=el('div',{id:'wb-result-launch'});
  if(r){const T=r.truth,t=T.map(x=>x.time_s),ex=r.exit,h=r.hand_check;
    out.append(card('Tube exit',badge(r.evidence_level),aeroIdentity(r.aero_dataset),el('div',{class:'wb-plots'},exitDiagram(ex,r.launch.heading_deg),
        table(['vector (NED)','north','east','down'],[['tube axis (unit)',...ex.tube_axis_N],['exit velocity (ft/s)',...ex.velocity_N_ft_s],['body x axis (unit)',...ex.body_x_axis_N],['body z axis (unit)',...ex.body_z_axis_N],['exit position (ft)',...ex.position_N_ft]],'wb-exit-table')),
        table(['hand check at exit','expected','observed'],Object.keys(h.expected).map(k=>[k,h.expected[k],h.observed[k]]),'wb-launch-handcheck'),el('p',{class:'wb-status',text:h.note+' '+r.frame+'.'})),
      card('Flight',el('p',{class:'wb-status',id:'wb-launch-status',text:`Status ${r.status}${r.stop_reason?' — '+r.stop_reason:''}; ${T.length} recorded instants; station loads recorded at each.`}),
        el('div',{class:'wb-plots'},
          plotCard({title:'Trajectory (vertical plane)',xlabel:'north (ft)',ylabel:'height AGL (ft)',series:[{name:'CG path',x:T.map(x=>x.position_N_ft[0]),y:T.map(x=>x.height_agl_ft)}]},'wb-plot-trajectory'),
          plotCard({title:'Plan view',xlabel:'north (ft)',ylabel:'east (ft)',series:[{name:'CG path',x:T.map(x=>x.position_N_ft[0]),y:T.map(x=>x.position_N_ft[1])}]},'wb-plot-plan'),
          plotCard({title:'Air-relative angles',xlabel:'time from tube exit (s)',ylabel:'deg',series:[{name:'alpha',x:t,y:T.map(x=>x.alpha_deg)},{name:'beta',x:t,y:T.map(x=>x.beta_deg)}]},'wb-plot-alpha'),
          plotCard({title:'Speed',xlabel:'time from tube exit (s)',ylabel:'airspeed (ft/s)',series:[{name:'airspeed',x:t,y:T.map(x=>x.airspeed_ft_s)}]},'wb-plot-speed'),
          plotCard({title:'Attitude and path',xlabel:'time from tube exit (s)',ylabel:'deg',series:[{name:'pitch θ',x:t,y:T.map(x=>x.theta_deg)},{name:'flight path',x:t,y:T.map(x=>x.flight_path_deg)},{name:'roll φ',x:t,y:T.map(x=>x.phi_deg)}]},'wb-plot-attitude'),
          plotCard({title:'Body rates',xlabel:'time from tube exit (s)',ylabel:'deg/s',series:['p','q','r'].map((n,i)=>({name:n,x:t,y:T.map(x=>x.omega_B_deg_s[i])}))},'wb-plot-rates'))),
      card('Evidence along the path',table(['from (s)','to (s)','evidence'],r.evidence_segments.map(s=>[s.start_s,s.end_s,s.evidence_level]),'wb-evidence-segments'),
        el('p',{class:'wb-status',text:'Every aerodynamic evaluation on the path carries the active dataset evidence level. A CFD dataset with partial coverage would show segments here.'})));}
  else out.appendChild(card(null,el('p',{class:'wb-status',text:'Run the launch to see exit vectors, the trajectory and alpha/beta from tube exit.'})));
  return [left,out];
}

// ---------------------------------------------------------------- station 9: load recovery
const LOAD_PARTS=['applied','gravity','inertia'];

function loadsRequest(){const run=wb.results.launch,used=wb.launchUsed,row=run.truth[wb.loadsCursor];
  return clone({...snapshotUpstream(used),controls:run.controls,state_SI:row.state_SI,time_s:row.time_s});}
async function evaluateLoads(){const run=wb.results.launch,cursor=wb.loadsCursor;if(!run||!wb.launchUsed||wb.savedLoadsRecord)return;
  const request=wb.loadsRequestToken=(wb.loadsRequestToken||0)+1,current=()=>wb.results.launch===run&&wb.loadsCursor===cursor&&wb.loadsRequestToken===request&&!wb.savedLoadsRecord;
  try{const r=await api('/run/loads',loadsRequest());if(!current())return;wb.results.loads=r;const box=document.getElementById('wb-loads-detail');if(box)box.replaceWith(loadsDetail());drawLoadHistory();}catch(e){if(current())showError('loads',e);}}
async function saveLoadsInstant(){const run=wb.results.launch,cursor=wb.loadsCursor;if(!run||wb.savedLoadsRecord)return;
  const rec=await saveStation('loads','loads',loadsRequest());
  if(rec&&wb.results.launch===run&&wb.loadsCursor===cursor&&!wb.savedLoadsRecord){wb.results.loads=rec.result;render();}}
function returnToLaunchLoads(){stopPlayback('loads');wb.savedLoadsRecord=null;wb.results.loads=null;wb.loadsRequestToken=(wb.loadsRequestToken||0)+1;render();evaluateLoads();}
// Free-body helpers for station 9. Every contribution below is already in the reaction
// convention (the force the rest of the aircraft exerts on the retained part), so
// total = applied + gravity + inertia holds component by component with no sign flips.
const FB_LIFTING=['wing-left','wing-right','tail-left','tail-right'];
function freeBodySummary(station){
  const sources=Object.keys(station.contributions?.applied_by_source||{});
  const inside=FB_LIFTING.filter(x=>sources.includes(x)),outside=FB_LIFTING.filter(x=>!sources.includes(x));
  return {inside,outside,others:sources.filter(x=>!FB_LIFTING.includes(x)).sort(),
          wingsOutside:['wing-left','wing-right'].filter(x=>outside.includes(x))};
}
function subRowOrderer(appliedBySource){
  const keys=Object.keys(appliedBySource||{});
  return [...FB_LIFTING.filter(k=>keys.includes(k)),...keys.filter(k=>!FB_LIFTING.includes(k)&&k!=='propulsion').sort(),...(keys.includes('propulsion')?['propulsion']:[])];
}
function balanceRows(station){
  const c=station.contributions;
  const row=(name,key,total,i,unit)=>{const g=c.gravity[key][i],a=c.applied[key][i],n=c.inertia[key][i];return {name,unit,gravity:g,applied:a,inertia:n,sum:g+a+n,total:total[i],residual:g+a+n-total[i]};};
  return [...['Fx','Fy','Fz'].map((n,i)=>row(n,'force_B_lbf',station.force_B_lbf,i,'lbf')),
          ...['Mx','My','Mz'].map((n,i)=>row(n,'moment_B_in_lbf',station.moment_B_in_lbf,i,'in-lbf'))];
}
function handCheckNumbers(station){
  // Numbers the user can add up by hand, all Fz in the reaction convention (z down, negative = upward push on the part).
  const c=station.contributions,by=c.applied_by_source||{},fz=v=>v?v.force_B_lbf[2]:0;
  const root=station.kind==='part'&&/^(wing|tail)-root-(left|right)$/.exec(station.id);
  if(root){
    const src=`${root[1]}-${root[2]}`,a=fz(by[src]),others=fz(c.applied)-a,g=fz(c.gravity),n=fz(c.inertia);
    return {type:'root',src,aero:a,otherApplied:others,gravity:g,inertia:n,sum:a+others+g+n,total:station.force_B_lbf[2]};
  }
  if(station.kind==='plane'){
    const weight=(c.retained?.masses||[]).reduce((t,m)=>t+m.weight_lbf,0);
    const summary=freeBodySummary(station),lifting=summary.inside.reduce((t,k)=>t+fz(by[k]),0);
    // Level-flight estimate: the cut holds up the retained weight, less whatever lift acts inside the free body.
    return {type:'plane',weight,liftingInside:lifting,estimate:-weight+lifting,total:station.force_B_lbf[2],gravity:fz(c.gravity)};
  }
  return null;
}
function handCheckText(hc){
  if(!hc)return '';
  if(hc.type==='root')return `Root hand check (Fz, reaction convention): ${hc.src} aero ${fmt(hc.aero)}`+(Math.abs(hc.otherApplied)>0?` + other applied ${fmt(hc.otherApplied)}`:'')+
    ` + gravity ${fmt(hc.gravity)} + inertia ${fmt(hc.inertia)} = ${fmt(hc.sum)} lbf; table total ${fmt(hc.total)} lbf. `+
    `Read it as: the root pushes the panel down by its lift (${fmt(-hc.aero)} lbf of aero Fz acts on the panel, negative = up) and holds up the panel's own weight (${fmt(-hc.gravity)} lbf along +z), so root shear is about lift minus panel weight.`;
  return `Cut hand check (Fz, level flight): retained weight ${fmt(hc.weight)} lbf; lifting-surface aero inside this free body gives ${fmt(hc.liftingInside)} lbf of reaction Fz; `+
    `estimate −W + lift = ${fmt(hc.estimate)} lbf against table total ${fmt(hc.total)} lbf (difference comes from pitch attitude, drag, thrust, body aero and acceleration).`;
}
function loadsSelectStation(id){
  if(wb.loadsStation&&wb.loadsStation!==id)wb.loadsPreviousStation=wb.loadsStation;
  wb.loadsStation=id;const box=document.getElementById('wb-loads-detail');if(box)box.replaceWith(loadsDetail());drawLoadHistory();
}
function fbdDiagram(r,selected){
  // Side view: horizontal = distance aft of the nose (in), vertical = up (-z). Arrows are reaction forces (Fx, Fz) on each retained part.
  const W=620,H=250,g=r.geometry,m=30,X=v=>m+(v/g.fuselage_length_in)*(W-2*m),Y=z=>H/2+z*6;
  const svg=svgEl('svg',{viewBox:`0 0 ${W} ${H}`,class:'wb-plot',id:'wb-fbd',role:'img','aria-label':'station reactions, side view'});
  svgEl('text',{x:8,y:14,class:'wb-plot-title'},svg).textContent='Reactions on the retained parts (side view, nose left; arrows Fx and Fz)';
  const sel=r.stations.find(x=>x.id===selected);
  if(sel&&sel.kind==='plane'){const [a,b]=sel.retain==='aft'?[sel.station_in,g.fuselage_length_in]:[0,sel.station_in];
    svgEl('title',{},svgEl('rect',{x:X(a),y:Y(0)-34,width:X(b)-X(a),height:68,class:'wb-fb-retained',id:'wb-fbd-retained'},svg)).textContent=`retained free body: ${sel.retain} of ${fmt(sel.station_in)} in`;}
  svgEl('line',{x1:X(0),x2:X(g.fuselage_length_in),y1:Y(0),y2:Y(0),class:'wb-frame','stroke-width':'6'},svg);
  svgEl('line',{x1:X(g.wing_le_station_in),x2:X(g.wing_le_station_in+g.wing_chord_in),y1:Y(0)-8,y2:Y(0)-8,class:'wb-series wb-s5'},svg);
  if(g.tail_chord_in)svgEl('line',{x1:X(g.tail_le_station_in),x2:X(g.tail_le_station_in+g.tail_chord_in),y1:Y(0)-8,y2:Y(0)-8,class:'wb-series wb-s5'},svg);
  for(const mass of sel?.contributions?.retained?.masses||[])
    svgEl('title',{},svgEl('rect',{x:X(-mass.position_B_in[0])-3,y:Y(mass.position_B_in[2])-3,width:6,height:6,class:'wb-fb-mass'},svg)).textContent=`${mass.name}: ${fmt(mass.weight_lbf)} lbf retained`;
  const fmax=Math.max(1e-9,...r.stations.map(s=>Math.hypot(s.force_B_lbf[0],s.force_B_lbf[2])));
  r.stations.forEach((s,k)=>{const sx=-s.reference_B_in[0],sz=s.reference_B_in[2],px=X(sx),py=Y(sz)+(s.kind==='part'?((k%3)-1)*4:0);
    const fx=s.force_B_lbf[0],fz=s.force_B_lbf[2],L=70/fmax,cls=s.id===selected?'wb-s1':'wb-s0';
    if(s.kind==='plane')svgEl('line',{x1:px,x2:px,y1:Y(0)-22,y2:Y(0)+22,class:'wb-cursor'},svg);
    svgEl('title',{},svgEl('circle',{cx:px,cy:py,r:s.id===selected?5:3,class:'wb-series '+cls},svg)).textContent=`${s.id}: F = [${vec(s.force_B_lbf).join(', ')}] lbf`;
    if(s.id===selected)svgEl('text',{x:px+6,y:py+(fz>0?-10:16),class:'wb-axis-label'},svg).textContent=s.id;
    svgEl('line',{x1:px,y1:py,x2:px-fx*L,y2:py+fz*L,class:'wb-series '+cls,'stroke-width':s.id===selected?'3':'1.5','data-station':s.id},svg);});
  svgEl('text',{x:8,y:H-8,class:'wb-tick'},svg).textContent=`Scale: longest arrow = ${fmt(fmax)} lbf. Shaded = retained free body; squares = retained masses; dashed verticals are fuselage cuts; orange is the selected station.`;
  return svg;}
function loadsDetail(){const r=wb.results.loads,box=el('div',{id:'wb-loads-detail'});if(!r)return el('div',{id:'wb-loads-detail',class:'wb-status',text:'Evaluating…'});
  if(r.status!=='ok'){box.appendChild(el('div',{class:'wb-error',text:r.status+': '+(r.error||'')}));return box;}
  const s=r.stations.find(x=>x.id===wb.loadsStation)||r.stations[0];wb.loadsStation=s.id;
  const pick=el('select',{id:'wb-loads-station',onchange:e=>loadsSelectStation(e.target.value)},...r.stations.map(x=>el('option',{value:x.id,selected:x.id===s.id,text:x.id})));
  const has=id=>r.stations.some(x=>x.id===id);
  const nav=el('div',{class:'wb-actions',id:'wb-loads-nav'},
    el('button',{class:'wb-btn wb-secondary',id:'wb-loads-goto-wing-left',disabled:!has('wing-root-left')||s.id==='wing-root-left',onclick:()=>loadsSelectStation('wing-root-left')},'Show wing-root-left'),
    el('button',{class:'wb-btn wb-secondary',id:'wb-loads-goto-wing-right',disabled:!has('wing-root-right')||s.id==='wing-root-right',onclick:()=>loadsSelectStation('wing-root-right')},'Show wing-root-right'),
    el('button',{class:'wb-btn wb-secondary',id:'wb-loads-goto-previous',disabled:!wb.loadsPreviousStation||!has(wb.loadsPreviousStation)||wb.loadsPreviousStation===s.id,onclick:()=>loadsSelectStation(wb.loadsPreviousStation)},'Back to '+(wb.loadsPreviousStation||'previous')));
  const c=s.contributions,by=c.applied_by_source||{};
  const rows=[['applied',...c.applied.force_B_lbf,...c.applied.moment_B_in_lbf],
    ...subRowOrderer(by).map(src=>['  applied: '+src,...by[src].force_B_lbf,...by[src].moment_B_in_lbf]),
    ...['gravity','inertia'].map(k=>[k,...c[k].force_B_lbf,...c[k].moment_B_in_lbf]),
    ['total reaction',...s.force_B_lbf,...s.moment_B_in_lbf],['reference recovery',...s.reference_check.force_B_lbf,...s.reference_check.moment_B_in_lbf]];
  const sum=[0,1,2].map(i=>LOAD_PARTS.reduce((a,k)=>a+c[k].force_B_lbf[i],0)),diff=Math.max(...sum.map((v,i)=>Math.abs(v-s.force_B_lbf[i])));
  const refDiff=Math.max(...[0,1,2].map(i=>Math.abs(s.force_B_lbf[i]-s.reference_check.force_B_lbf[i])),...[0,1,2].map(i=>Math.abs(s.moment_B_in_lbf[i]-s.reference_check.moment_B_in_lbf[i])));
  const fs=freeBodySummary(s);
  const where=s.kind==='plane'?`Everything ${s.retain} of the fuselage cut at ${fmt(s.station_in)} in aft of the nose.`:`The ${s.id} part only.`;
  const lifting=`Lifting surfaces inside: ${fs.inside.join(', ')||'none'}. Outside: ${fs.outside.join(', ')||'none'}.`+(fs.others.length?` Other applied sources inside: ${fs.others.join(', ')}.`:'');
  const note=s.kind==='plane'&&fs.wingsOutside.length?` Wing lift (${fs.wingsOutside.join(', ')}) acts outside this free body; it reaches this cut only through the structure, as part of the reaction below.`:'';
  const masses=c.retained?.masses||[],elements=c.retained?.applied_elements||[],weight=masses.reduce((t,m)=>t+m.weight_lbf,0);
  const retained=el('details',{id:'wb-loads-retained',open:true},el('summary',{text:`Contents of this free body: ${masses.length} mass pieces (${fmt(weight)} lbf), ${elements.length} applied elements`}),
    table(['mass piece','retained weight (lbf)','station (in aft of nose)','z (in)'],[...masses.map(m=>[m.name,m.weight_lbf,-m.position_B_in[0],m.position_B_in[2]]),['total',weight,'','']],'wb-loads-masses'),
    table(['applied element','source','retained fraction','station (in aft of nose)','Fx (lbf)','Fy (lbf)','Fz (lbf)'],elements.map(e=>[e.name,e.source,e.weight,-e.point_B_in[0],...e.force_B_lbf]),'wb-loads-elements'),
    el('p',{class:'wb-status',text:'Element forces are as applied to the aircraft (not reaction-signed); negative Fz is lift. The applied row above is minus their sum.'}));
  box.append(el('label',{class:'wb-field wb-wide'},'Station',pick),el('p',{class:'wb-status',text:s.description+(s.kind==='plane'?` (cut at ${fmt(s.station_in)} in aft of nose, ${s.retain} part retained)`:'')+`; moments about [${vec(s.reference_B_in).join(', ')}] in.`}),
    nav,aeroIdentity(r.aero_dataset),fbdDiagram(r,s.id),
    el('p',{class:'wb-status',id:'wb-loads-inside',text:where+' '+lifting+note}),
    el('p',{class:'wb-status',id:'wb-loads-convention',text:'Rows are reactions on the retained part from the rest of the aircraft: body axes x forward, y right, z down, so negative Fz pushes the part upward. Applied + gravity + inertia = total reaction.'}),
    table(['part','Fx (lbf)','Fy (lbf)','Fz (lbf)','Mx (in-lbf)','My (in-lbf)','Mz (in-lbf)'],rows,'wb-loads-contrib'),
    el('p',{class:'wb-status',id:'wb-loads-balance',text:'Balance at this instant: '+balanceRows(s).map(b=>`${b.name}: ${fmt(b.gravity)} + ${fmt(b.applied)} + ${fmt(b.inertia)} = ${fmt(b.sum)} ${b.unit} (total ${fmt(b.total)}, residual ${fmt(b.residual,2)})`).join('; ')+'. Order: gravity + applied + inertia.'}),
    el('p',{class:'wb-status',id:'wb-loads-handcheck',text:handCheckText(handCheckNumbers(s))}),
    el('p',{class:'wb-status',id:'wb-loads-check',text:`Hand check: applied + gravity + inertia − total = ${fmt(diff,2)} lbf; fast recovery − balanced reference recovery = ${fmt(refDiff,2)} (lbf, in-lbf). Whole aircraft balanced: ${r.whole_body.balanced?'yes':'NO'} (residual ${fmt(Math.max(...r.whole_body.force_residual_lbf.map(Math.abs)),2)} lbf).`}),
    retained,
    el('details',{},el('summary',{text:'All stations at this instant'}),table(['station','Fx','Fy','Fz','Mx','My','Mz'],r.stations.map(x=>[x.id,...x.force_B_lbf,...x.moment_B_in_lbf]),'wb-loads-all')),
    el('p',{class:'wb-status',text:r.convention}));
  return box;}
function drawLoadHistory(){const run=wb.results.launch,box=document.getElementById('wb-loads-history');if(!run||!box||wb.savedLoadsRecord)return;const sid=wb.loadsStation||'wing-root-left',t=run.station_loads.map(x=>x.time_s),cx=run.truth[wb.loadsCursor]?.time_s;
  const pick=k=>run.station_loads.map(x=>{const s=x.stations.find(y=>y.id===sid);return s?s[k]:[null,null,null];});const F=pick('force_B_lbf'),M=pick('moment_B_in_lbf');box.textContent='';
  box.append(plotCard({title:sid+' force history',xlabel:'time from tube exit (s)',ylabel:'lbf',cursorX:cx,height:210,series:['Fx','Fy','Fz'].map((n,i)=>({name:n,x:t,y:F.map(v=>v[i])}))},'wb-plot-load-force'),
    plotCard({title:sid+' moment history',xlabel:'time from tube exit (s)',ylabel:'in-lbf',cursorX:cx,height:210,series:['Mx','My','Mz'].map((n,i)=>({name:n,x:t,y:M.map(v=>v[i])}))},'wb-plot-load-moment'));}
function loadsView(){
  const sid='loads',run=wb.results.launch;
  if(wb.savedLoadsRecord){const rec=wb.savedLoadsRecord;return [
    el('div',{},card('Saved instant',el('p',{id:'wb-loads-saved-context',text:`${rec.label||'Untitled'} — ${rec.id}; t = ${fmt(rec.inputs.time_s)} s. Standalone saved result; no live trajectory is attached.`}),badge(rec.result.evidence_level),el('button',{class:'wb-btn wb-secondary',id:'wb-loads-return',onclick:returnToLaunchLoads},'Return to launch run')),card('Saved runs',el('div',{id:'wb-saved-'+sid}))),
    card('Free-body view at saved instant',loadsDetail())];}
  const left=el('div',{},card('Run record',el('p',{class:'wb-status',text:'Station 9 recovers concurrent loads at every station from a run. It uses the station 7 launch run; move the cursor to any recorded instant.'}),
    run&&wb.launchUsed?el('p',{id:'wb-loads-source',text:`Launch: ${run.launch.tube_elevation_deg} deg, ${run.launch.exit_speed_ft_s} ft/s, ${run.truth.length} instants, status ${run.status}.`}):
      el('div',{},el('p',{class:'wb-status',text:'No launch run yet.'}),el('button',{class:'wb-btn',id:'wb-loads-run-launch',onclick:async()=>{const body=clone(launchBody());const x=await runStation(sid,'launch',body);if(x){setLaunchRun(x,body);render();evaluateLoads();}}},'Run station 7 with its current inputs')),
    actions(sid,run&&wb.launchUsed?[el('button',{class:'wb-btn wb-secondary',id:'wb-save-loads',onclick:saveLoadsInstant},'Save this instant')]:[])),
    card('Saved runs',el('div',{id:'wb-saved-'+sid})));
  const out=el('div',{id:'wb-result-loads'});
  if(run&&wb.launchUsed){const n=run.truth.length;wb.loadsCursor=Math.min(wb.loadsCursor||0,n-1);const row=run.truth[wb.loadsCursor];
    const label=r=>`t = ${fmt(r.time_s)} s, height ${fmt(r.height_agl_ft)} ft, airspeed ${fmt(r.airspeed_ft_s)} ft/s, α ${fmt(r.alpha_deg)} deg`;
    const player=cursorPlayer('loads',()=>wb.results.launch,()=>wb.loadsCursor,setLoadsCursor);
    const slider=el('input',{type:'range',id:'wb-loads-slider',min:'0',max:String(n-1),step:'1',value:String(wb.loadsCursor),oninput:e=>{stopPlayback('loads');setLoadsCursor(e.target.value);}});
    out.append(card('Time cursor',badge(run.evidence_level),player,slider,
        el('div',{class:'wb-status',id:'wb-loads-cursor-label',text:label(row)})),
      card('Free-body view at the cursor',loadsDetail()),card('Histories',el('div',{class:'wb-plots',id:'wb-loads-history'})));
    if(!wb.results.loads)setTimeout(evaluateLoads,0);}
  else out.appendChild(card(null,el('p',{class:'wb-status',text:'Run a launch (station 7) to recover station loads along it.'})));
  return [left,out];
}

// ---------------------------------------------------------------- station 10: Monte Carlo
const MC_OUTCOMES={min_height_ft:'min height (ft)',max_rate_deg_s:'max body rate (deg/s)',min_airspeed_ft_s:'min airspeed (ft/s)',max_airspeed_ft_s:'max airspeed (ft/s)',wing_root_moment:'wing-root-left peak |M| (in-lbf)'};
function mcOutcome(sm,key){if(key==='wing_root_moment')return sm.station_peaks?.['wing-root-left']?.moment?.magnitude??null;return sm[key];}
async function mcLoadList(){try{const r=await api('/studies');wb.mc.list=r.studies;if(!wb.mc.study)wb.mc.study=r.default_study;}catch(e){showError('monte-carlo',e);}}
async function mcOpen(id){clearTimeout(wb.mc.timer);const token=wb.mc.selection=(wb.mc.selection||0)+1;wb.mc.open=id;wb.mc.detail=null;wb.mc.replay=null;renderMc();try{const detail=await api('/studies/'+id);if(token!==wb.mc.selection)return;wb.mc.detail=detail;renderMc();if(detail.meta.status==='running')mcPoll();}catch(e){if(token===wb.mc.selection)showError('monte-carlo',e);}}
function mcPoll(){clearTimeout(wb.mc.timer);const id=wb.mc.open,token=wb.mc.selection;wb.mc.timer=setTimeout(async()=>{if(!id||token!==wb.mc.selection)return;try{const detail=await api('/studies/'+id);if(token!==wb.mc.selection)return;wb.mc.detail=detail;}catch(e){if(token===wb.mc.selection)showError('monte-carlo',e);return;}renderMc();if(wb.mc.detail.meta.status==='running')mcPoll();else mcLoadList().then(renderMc);},1000);}
function mcSyncDraft(){
  const st=wb.mc.study,seed=document.getElementById('wb-mc-study-seed'),count=document.getElementById('wb-mc-study-count'),variables=document.getElementById('wb-mc-variables');
  const next={seed:seed?Number(seed.value):st.seed,count:count?Number(count.value):st.count,variables:variables?JSON.parse(variables.value):st.variables};
  Object.assign(st,next);return clone(st);
}
function mcSubmission(study,label){
  const body={label,study},origin=wb.mc.baseImport;
  if(origin&&JSON.stringify(study.base)===JSON.stringify(origin.base))body.base_json=origin.base_json;
  return body;
}
async function mcImportCapture(kind,id){
  if(wb.busy)return;showError('monte-carlo',null);
  let study,source;try{study=mcSyncDraft();source=kind==='current-capture'?{kind,inputs:clone(captureBody())}:{kind,id:id??document.getElementById('wb-mc-saved-run').value.trim()};}
  catch(e){showError('monte-carlo',e);return;}
  setBusy(true);
  try{const draft=await api('/studies/draft',{study,source});
    if(JSON.stringify(mcSyncDraft())!==JSON.stringify(study))throw Error('Study draft changed during import; import again to replace its base.');
    wb.mc.study=draft.study;wb.mc.baseImport={base:clone(draft.study.base),base_json:draft.base_json,base_inputs_sha256:draft.base_inputs_sha256,source:draft.source};render();
  }catch(e){showError('monte-carlo',e);}finally{setBusy(false);renderMc();}
}
async function mcStart(){
  if(wb.busy)return;showError('monte-carlo',null);let body;
  try{body=mcSubmission(mcSyncDraft(),document.getElementById('wb-label-monte-carlo')?.value||'');}catch(e){showError('monte-carlo',e);return;}
  setBusy(true);
  try{const meta=await api('/studies',body);await mcLoadList();await mcOpen(meta.id);}
  catch(e){showError('monte-carlo',e);}finally{setBusy(false);renderMc();}
}
async function mcCancel(){try{await api('/studies/'+wb.mc.open+'/cancel',{});}catch(e){showError('monte-carlo',e);}}
async function mcReplay(index){showError('monte-carlo',null);const box=document.getElementById('wb-mc-replay');if(box)box.textContent='Replaying sample '+index+' through the production runner…';
  const selection=wb.mc.selection,id=wb.mc.open,token=wb.mc.replayRequest=(wb.mc.replayRequest||0)+1;
  try{const replay=await api('/studies/'+id+'/replay/'+index,{});if(selection!==wb.mc.selection||token!==wb.mc.replayRequest)return;wb.mc.replay=replay;renderMc();document.getElementById('wb-mc-replay')?.scrollIntoView({block:'nearest'});}catch(e){if(selection!==wb.mc.selection||token!==wb.mc.replayRequest)return;showError('monte-carlo',e);if(box)box.textContent='';}}
function mcEditor(){const st=wb.mc.study;if(!st)return el('p',{class:'wb-status',text:'Loading study defaults…'});
  const sc=st.base.bench.launch.scenario,timing=st.base.bench.timing;
  return el('div',{},
    el('fieldset',{},el('legend',{text:'Explicit capture configuration import'}),
      el('p',{class:'wb-status',id:'wb-mc-base-source',text:wb.mc.baseImport?`Imported from ${wb.mc.baseImport.source}; base inputs ${wb.mc.baseImport.base_inputs_sha256}. Later upstream edits do not alter this snapshot. Local base edits receive a new identity on submission.`:'Default independent study base. Import explicitly to use Station 8 settings; no live coupling.'}),
      el('button',{class:'wb-btn wb-secondary',id:'wb-mc-import-current',onclick:()=>mcImportCapture('current-capture')},'Import current capture editors'),
      el('label',{class:'wb-field'},'Saved controller run UUID',el('input',{id:'wb-mc-saved-run',type:'text'})),
      el('button',{class:'wb-btn wb-secondary',id:'wb-mc-import-saved',onclick:()=>mcImportCapture('saved-run')},'Import saved controller run')),
    el('fieldset',{},el('legend',{text:'Study'}),buildForm({seed:st.seed,count:st.count},'wb-mc-study'),buildForm(st.budget,'wb-mc-budget'),el('p',{class:'wb-status',text:'max_run_seconds is advisory metadata, not a wall-clock cutoff. Cancellation is cooperative; samples already in flight may finish.'})),
    el('fieldset',{},el('legend',{text:'Base launch scenario (edits the base inputs)'}),buildForm(sc,'wb-mc-scenario'),buildForm(timing,'wb-mc-timing')),
    el('fieldset',{},el('legend',{text:'Variables (JSON): distribution, params, bindings into the base inputs'}),
      el('textarea',{class:'wb-json',id:'wb-mc-variables',spellcheck:'false'},JSON.stringify(st.variables,null,1)),
      el('p',{class:'wb-status',text:'Distributions: uniform{low,high}, normal{mean,std}, truncated-normal{mean,std,low,high}, discrete{values,weights}. Transforms: set, add, scale. Same "latent" id links variables by rank; "reverse": true reverses the link.'})),
    el('div',{class:'wb-actions'},el('button',{class:'wb-btn',id:'wb-mc-start',onclick:mcStart},'Start study'),
      el('button',{class:'wb-btn wb-secondary',id:'wb-mc-cancel',disabled:!(wb.mc.detail&&wb.mc.detail.meta.status==='running'),onclick:mcCancel},'Cancel running study')),
    el('label',{class:'wb-field'},'Label',el('input',{type:'text',id:'wb-label-monte-carlo',maxlength:'120'})),
    el('div',{class:'wb-error',id:'wb-error-monte-carlo',role:'alert'}));}
function mcResults(){const box=el('div',{id:'wb-mc-results'}),d=wb.mc.detail;
  if(!d){box.appendChild(card(null,el('p',{class:'wb-status',text:'Start a study, or open a saved one.'})));return box;}
  const vars=d.study.variables.map(v=>v.id),S=d.samples,status={};for(const sm of S)status[sm.status]=(status[sm.status]||0)+1;
  const captured=S.filter(sm=>sm.capture&&sm.capture.met).length;
  box.appendChild(card(`Study ${d.meta.label||d.meta.name}`,el('progress',{id:'wb-mc-progress',max:String(d.progress.total),value:String(d.progress.completed)}),
    el('p',{class:'wb-status',id:'wb-mc-status',text:`${d.meta.status}: ${d.progress.completed} of ${d.progress.total} samples; seed ${d.study.seed}; study sha256 ${d.meta.study_sha256.slice(0,16)}…${d.meta.error?' — '+d.meta.error:''}`}),
    el('p',{class:'wb-status',id:'wb-mc-submitted-base',text:`Submitted base inputs: ${d.meta.base_inputs_sha256||'not recorded in this older archive'}; source: ${d.meta.source||d.study.source}. Results use this saved study, not current editors.`}),
    ...[...new Set(S.filter(sm=>sm.status==='input-rejected').map(sm=>sm.stop_reason))].slice(0,3).map(r=>el('p',{class:'wb-error',text:'Input rejected: '+r})),
    el('p',{id:'wb-mc-outcomes',text:`Outcomes: ${Object.entries(status).map(([k,n])=>k+' '+n).join(', ')||'none yet'}; sustained capture ${captured} of ${S.length}.`}),
    el('p',{class:'wb-status',text:'Frequencies describe the declared input model, not the real world. Rare governing cases need many samples: with n samples, an event of probability p is seen at least once with probability 1 − (1 − p)^n.'})));
  if(S.length){const xk=wb.mc.x&&vars.includes(wb.mc.x)?wb.mc.x:vars[0],yk=wb.mc.y||'wing_root_moment';wb.mc.x=xk;wb.mc.y=yk;
    const xs=el('select',{id:'wb-mc-x',onchange:e=>{wb.mc.x=e.target.value;renderMc();}},...vars.map(v=>el('option',{value:v,selected:v===xk,text:v})));
    const ys=el('select',{id:'wb-mc-y',onchange:e=>{wb.mc.y=e.target.value;renderMc();}},...Object.entries(MC_OUTCOMES).map(([k,l])=>el('option',{value:k,selected:k===yk,text:l})));
    const groups=[['captured',sm=>sm.capture&&sm.capture.met],['not captured',sm=>!(sm.capture&&sm.capture.met)&&sm.status==='completed'],['stopped (ground/limit)',sm=>sm.status!=='completed']];
    const scatter=groups.map(([name,f])=>{const g=S.filter(f);return {name,scatter:true,x:g.map(sm=>sm.values[xk]),y:g.map(sm=>mcOutcome(sm,yk)),ids:g.map(sm=>sm.index),labels:g.map(sm=>`sample ${sm.index}: ${sm.status}`),onclick:i=>mcReplay(g[i].index)};});
    const plot=el('div',{id:'wb-mc-scatter'});plotLines(plot,{title:'Samples (click a point to replay it)',xlabel:xk,ylabel:MC_OUTCOMES[yk],series:scatter});
    box.appendChild(card('Explore',el('div',{class:'wb-actions'},el('label',{class:'wb-field wb-wide'},'x',xs),el('label',{class:'wb-field wb-wide'},'y',ys)),plot));
    box.appendChild(card('Samples',el('div',{class:'wb-scroll'},el('table',{class:'wb-table',id:'wb-mc-table'},el('thead',{},el('tr',{},...['#',...vars,'status','captured','min height (ft)','max rate (deg/s)','wing-root-left |M| (in-lbf)',''].map(h=>el('th',{text:h})))),
      el('tbody',{},...S.map(sm=>el('tr',{class:wb.mc.replay&&wb.mc.replay.index===sm.index?'wb-selected':null},el('td',{text:String(sm.index)}),...vars.map(v=>el('td',{text:fmt(sm.values[v])})),el('td',{text:sm.status,title:sm.stop_reason||''}),el('td',{text:sm.capture&&sm.capture.met?'yes':'no'}),el('td',{text:fmt(sm.min_height_ft)}),el('td',{text:fmt(sm.max_rate_deg_s)}),el('td',{text:fmt(mcOutcome(sm,'wing_root_moment'))}),
        el('td',{},el('button',{class:'wb-btn wb-secondary','data-replay':String(sm.index),onclick:()=>mcReplay(sm.index)},'Replay')))))))));}
  const r=wb.mc.replay,rep=el('div',{id:'wb-mc-replay'});
  if(r){const T=r.truth,t=T.map(x=>x.time_s);
    rep.append(el('p',{id:'wb-mc-replay-match',class:r.summary_matches?'wb-ok':'wb-bad',text:r.summary_matches?`Replay of sample ${r.index} reproduces its stored summary exactly (report sha256 ${r.report_sha256.slice(0,16)}…).`:`Replay of sample ${r.index} does NOT match its stored summary (replayed ${r.report_sha256?.slice(0,16)}, stored ${String(r.stored_report_sha256).slice(0,16)}).`}),
      badge(r.evidence_level),aeroIdentity(r.aero_dataset),el('p',{class:'wb-status',text:`Status ${r.status}${r.stop_reason?' — '+r.stop_reason:''}. Values: ${Object.entries(r.summary.values).map(([k,v])=>k+' = '+fmt(v)).join(', ')}.`}),
      el('div',{class:'wb-plots'},plotCard({title:'Height',xlabel:'time (s)',ylabel:'ft',height:200,series:[{name:'height',x:t,y:T.map(x=>x.height_ft)}]},'wb-mc-plot-height'),
        plotCard({title:'Airspeed and angles',xlabel:'time (s)',ylabel:'ft/s, deg',height:200,series:[{name:'airspeed',x:t,y:T.map(x=>x.airspeed_ft_s)},{name:'alpha',x:t,y:T.map(x=>x.alpha_deg)},{name:'flight path',x:t,y:T.map(x=>x.flight_path_deg)}]},'wb-mc-plot-air')),
      el('details',{},el('summary',{text:`Events (${(r.events||[]).length})`}),el('pre',{text:JSON.stringify(r.events||[],null,1)})));}
  box.appendChild(card('Replay',rep));
  return box;}
function renderMc(){if(wb.current!=='monte-carlo')return;const old=document.getElementById('wb-mc-results');if(old)old.replaceWith(mcResults());const list=document.getElementById('wb-mc-list');if(list)list.replaceWith(mcList());const c=document.getElementById('wb-mc-cancel');if(c)c.disabled=!(wb.mc.detail&&wb.mc.detail.meta.status==='running');}
function mcList(){const box=el('div',{id:'wb-mc-list'});if(!wb.mc.list.length){box.appendChild(el('p',{class:'wb-status',text:'No studies yet.'}));return box;}
  const pick=el('select',{id:'wb-mc-pick'},...wb.mc.list.map(m=>el('option',{value:m.id,selected:m.id===wb.mc.open,text:`${m.created_utc} ${m.label||m.name} (${m.status}, ${m.progress.completed}/${m.count})`})));
  box.append(pick,el('div',{class:'wb-actions'},el('button',{class:'wb-btn wb-secondary',id:'wb-mc-open',onclick:()=>mcOpen(pick.value)},'Open study')));return box;}
function monteCarloView(){if(!wb.mc){wb.mc={list:[],study:null,detail:null,open:null,replay:null};}
  const left=el('div',{},card('Study definition',mcEditor()),card('Studies',mcList()));
  if(!wb.mc.study&&!wb.mc.loading){wb.mc.loading=true;mcLoadList().then(()=>{if(wb.mc.study)render();});}
  return [left,mcResults()];}

// ---------------------------------------------------------------- station 8: controller & capture
const CB_API='/api/aircraft/controller-bench',CB_SELECTED='flightloads.controller-bench.selected';
async function cbCall(path,body){const opt=body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)};const r=await fetch(CB_API+path,opt);const d=await r.json().catch(()=>({}));if(!r.ok)throw Error(d.error||('HTTP '+r.status));return d;}
function captureBody(){const c=wb.inputs.capture;return {...upstream(),bench:clone(c.bench)};}
async function captureRun(){if(wb.busy)return;const sid='capture';showError(sid,null);setBusy(true);const status=document.getElementById('wb-status-'+sid);
  try{if(status)status.textContent='Preparing inputs…';
    const pr=await fetch('/api/workbench/run/capture',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(captureBody())});const raw=await pr.text();
    if(!pr.ok)throw Error((()=>{try{return JSON.parse(raw).error;}catch{return 'HTTP '+pr.status;}})());
    // Forward the prepared text verbatim: the controller API checks digests of the canonical JSON.
    const jr=await fetch(CB_API+'/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:'{"inputs":'+raw+',"parent_config_id":null}'});const job=await jr.json();if(!jr.ok)throw Error(job.error||('HTTP '+jr.status));wb.capture={id:job.id,record:job,report:null};
    for(;;){await new Promise(r=>setTimeout(r,500));const rec=await cbCall('/records/'+job.id);wb.capture.record=rec;
      if(status)status.textContent=`Controller bench record ${job.id.slice(0,8)}…: ${rec.phase}${rec.progress&&rec.progress.time_s!=null?' at t = '+fmt(rec.progress.time_s)+' s':''}`;
      if(['finished','failed','interrupted'].includes(rec.phase))break;}
    if(wb.capture.record.phase!=='finished')throw Error('Run '+wb.capture.record.phase+': '+(wb.capture.record.error||''));
    wb.capture.report=await cbCall('/records/'+job.id+'/report');render();}
  catch(e){showError(sid,e);}finally{setBusy(false);}}
async function captureExample(){if(wb.busy)return;const before=JSON.stringify(captureBody());showError('capture',null);setBusy(true);
  try{const example=await api('/controller-example');if(JSON.stringify(captureBody())!==before)throw Error('Capture editors changed during example load; apply again to replace them.');wb.inputs.capture.bench=clone(example.inputs.bench);wb.inputs.propulsion.propulsion=clone(example.inputs.propulsion);wb.options.atmosphere=false;wb.options.propulsion=true;wb.aeroBinding=null;wb.captureExampleNote=example.note;render();}
  catch(e){showError('capture',e);}finally{setBusy(false);}}
async function captureDesign(){if(wb.busy)return;const body=clone(captureBody());showError('capture',null);setBusy(true);
  try{wb.captureDesign=clone(await api('/run/controller-design',body));render();}
  catch(e){showError('capture',e);}finally{setBusy(false);}}
function captureDesignCard(){const r=wb.captureDesign;if(!r)return null;
  return card('Sampled pitch-loop diagnostic',el('div',{id:'wb-capture-design-result'},
    el('p',{class:'wb-status',style:'overflow-wrap:anywhere',text:`Inputs ${r.inputs_sha256}; controller period ${r.controller_period_s} s; servo delay ${r.servo_delay_s} s. This saved diagnostic uses its own input snapshot, not later editor changes.`}),
    table(['loop','pole classification','spectral radius'],[['rate only',r.inner_rate_loop.pole_classification,r.inner_rate_loop.spectral_radius],['attitude + rate',r.attitude_loop.pole_classification,r.attitude_loop.spectral_radius]],'wb-design-poles'),
    table(['loop','gain crossing (rad/s)','direction','phase margin (deg)'],['inner_rate_loop','attitude_loop'].flatMap(k=>r[k].gain_crossings.map(x=>[k,x.frequency_rad_s,x.gain_direction,x.phase_margin_deg])),'wb-design-margins'),
    el('p',{class:'wb-status',text:`Gain margins (dB): rate ${r.inner_rate_loop.gain_margins_db.map(fmt).join(', ')||'no crossing'}; attitude ${r.attitude_loop.gain_margins_db.map(fmt).join(', ')||'no crossing'}. Multiple crossings require inspection; a margin alone is not a stability certificate. Derivative refinement max error ${fmt(r.max_derivative_refinement_error)}; commanded pitch-rate bound ${r.commanded_rate_limit_deg_s} deg/s.`}),
    table(['surface','rate (deg/s)','min (deg)','max (deg)','allocation deg/(deg/s²)'],r.servo_limits.map(s=>[s.surface,s.rate_deg_s,s.travel_min_deg,s.travel_max_deg,s.allocation_deg_per_deg_s2]),'wb-design-servos'),
    el('div',{class:'wb-plots'},plotCard({title:'Attitude-loop gain',xlabel:'omega (rad/s)',ylabel:'dB',series:[{name:'sampled loop',x:r.attitude_loop.frequency_rad_s,y:r.attitude_loop.gain_db}]},'wb-design-gain'),plotCard({title:'Attitude-loop phase',xlabel:'omega (rad/s)',ylabel:'deg',series:[{name:'sampled loop',x:r.attitude_loop.frequency_rad_s,y:r.attitude_loop.phase_deg}]},'wb-design-phase')),
    ...r.limitations.map(text=>el('p',{class:'wb-warn',text})),el('details',{},el('summary',{text:'Frozen plant matrices and input snapshot'}),el('pre',{class:'wb-json',text:JSON.stringify({A:r.A,B:r.B,inputs:r.inputs},null,1)}))));}
function captureView(){
  const sid='capture',b=wb.inputs.capture.bench,L=b.launch;
  const left=el('div',{},card('Inputs',upstreamOptions(),
      el('p',{class:'wb-warn',text:'Opt-in strip-model worked example: replaces capture and upstream editors, not saved results or study drafts. 4/16 reserved captures; not broad-envelope design acceptance.'}),
      el('div',{class:'wb-actions'},el('button',{class:'wb-btn wb-secondary',id:'wb-capture-example',onclick:captureExample},'Load strip-model worked example'),el('button',{class:'wb-btn wb-secondary',id:'wb-capture-design',onclick:captureDesign},'Analyze sampled pitch loop')),
      wb.captureExampleNote?el('p',{class:'wb-status',text:wb.captureExampleNote}):null,
      el('fieldset',{},el('legend',{text:'Tube exit'}),buildForm(L.scenario,'wb-in-capture-scenario')),
      el('fieldset',{},el('legend',{text:'Launch phases: rate damping, then attitude recovery'}),buildForm({rate_damp:L.rate_damp,attitude:L.attitude},'wb-in-capture-phases')),
      el('fieldset',{},el('legend',{text:'Inner loop (rate-attitude) gains and limits'}),buildForm(b.controller,'wb-in-capture-controller')),
      el('fieldset',{},el('legend',{text:'Servo travel, slew rate and delay'}),buildForm(b.actuators,'wb-in-capture-actuators')),
      L.speed_recovery?el('fieldset',{},el('legend',{text:'Speed-recovery phase'}),buildForm(L.speed_recovery,'wb-in-capture-speed-recovery')):null,
      b.schedule?el('fieldset',{},el('legend',{text:'Dynamic-pressure schedule'}),buildForm(b.schedule,'wb-in-capture-schedule')):null,
      el('fieldset',{},el('legend',{text:'Outer loop (capture: airspeed by thrust, flight path by pitch)'}),buildForm(b.outer_loop||{},'wb-in-capture-outer')),
      el('fieldset',{},el('legend',{text:'Capture target and criteria'}),buildForm({target:b.target,criteria:b.criteria,timing:b.timing},'wb-in-capture-bench')),
      el('div',{},el('div',{class:'wb-actions'},el('button',{class:'wb-btn',id:'wb-run-capture',onclick:captureRun},'Run launch to capture')),el('div',{class:'wb-status',id:'wb-status-capture'}),el('div',{class:'wb-error',id:'wb-error-capture',role:'alert'})),
      el('p',{class:'wb-status',text:'Runs go through the controller bench (same controller code, same saved-record catalog), so every run here also opens in the controller learning, replay and comparison views.'})));
  const out=el('div',{id:'wb-result-capture'}),c=wb.capture;
  const diagnostic=captureDesignCard();if(diagnostic)out.appendChild(diagnostic);
  if(c?.report)out.appendChild(el('p',{class:'wb-status',style:'overflow-wrap:anywhere',text:`Saved capture record ${c.id}; inputs ${c.report.inputs_sha256}. The outcome below is from that record, not the current editors or newly loaded example. Run again to evaluate changed settings.`}));
  if(c&&c.report&&!c.report.truth.length){out.appendChild(card('Outcome',el('div',{class:'wb-warn',id:'wb-capture-status',text:`No flight: ${c.report.status} — ${c.report.stop_reason||''}. The capture target must be a trimmable level-flight condition; with the estimated propeller map, level trim exists only near 120–140 ft/s (#199 F1).`})));}
  else if(c&&c.report){const r=c.report,T=r.truth,t=T.map(x=>x.time_s),tg=r.inputs.bench.target,cr=r.inputs.bench.criteria;
    const phases=(r.events||[]).filter(e=>['launch-exit','launch-phase','completed','stopped','ground','controller-mode'].includes(e.kind)||e.kind.startsWith('stop'));
    const outcome=r.outcomes||{};
    out.append(card('Outcome',badge((r.aero_dataset||{}).evidence_level),aeroIdentity(r.aero_dataset),el('p',{id:'wb-capture-status',text:`Record ${c.id} — status ${r.status}${r.stop_reason?' ('+r.stop_reason+')':''}.`}),
        table(['objective','met','first entry (s)','final dwell (s)','reason'],Object.entries(outcome).map(([k,o])=>[k,o.met?'yes':'no',o.first_entry_time_s,o.final_dwell_duration_s,o.reason||'']),'wb-capture-outcomes'),
        el('div',{class:'wb-actions'},el('button',{class:'wb-btn wb-secondary',id:'wb-capture-open-legacy',onclick:()=>{try{localStorage.setItem(CB_SELECTED,c.id);}catch{}location.href='/aircraft#controller-bench-panel';}},'Open in controller learning views'))),
      card('Mode timeline',table(['time (s)','event','detail','trigger','max rate (deg/s)','attitude error (deg)'],phases.map(e=>[e.time_s,e.kind,e.reason||'',e.trigger||'',e.max_rate_deg_s,e.attitude_error_deg]),'wb-capture-timeline'),
        el('p',{class:'wb-status',text:'Rate damping stops the tip-off rotation; attitude recovery rotates to the trim attitude; capture regulates airspeed with thrust and flight path with pitch. Each hand-over is on criteria (held for the dwell) or on timeout.'})),
      el('div',{class:'wb-plots'},
        plotCard({title:'Height',xlabel:'time from tube exit (s)',ylabel:'ft',height:210,series:[{name:'height',x:t,y:T.map(x=>x.height_ft)}]},'wb-capture-plot-height'),
        plotCard({title:'Airspeed and capture band',xlabel:'time from tube exit (s)',ylabel:'ft/s',height:210,series:[{name:'airspeed',x:t,y:T.map(x=>x.airspeed_ft_s)},{name:'target + band',x:[t[0],t.at(-1)],y:[tg.airspeed_ft_s+cr.speed_error_ft_s,tg.airspeed_ft_s+cr.speed_error_ft_s],dash:true},{name:'target − band',x:[t[0],t.at(-1)],y:[tg.airspeed_ft_s-cr.speed_error_ft_s,tg.airspeed_ft_s-cr.speed_error_ft_s],dash:true}]},'wb-capture-plot-speed'),
        plotCard({title:'Flight path and alpha',xlabel:'time from tube exit (s)',ylabel:'deg',height:210,series:[{name:'flight path',x:t,y:T.map(x=>x.flight_path_deg)},{name:'alpha',x:t,y:T.map(x=>x.alpha_deg)}]},'wb-capture-plot-path'),
        plotCard({title:'Attitude error and peak body rate',xlabel:'time from tube exit (s)',ylabel:'deg, deg/s',height:210,series:[{name:'attitude error',x:t,y:T.map(x=>x.attitude_error_deg)},{name:'max body rate',x:t,y:T.map(x=>x.rate_max_deg_s)}]},'wb-capture-plot-attitude'),
        plotCard({title:'Surfaces (actual)',xlabel:'time from tube exit (s)',ylabel:'deg',height:210,series:[['left tail','left_tail_deg'],['right tail','right_tail_deg'],['aileron','aileron_deg']].map(([n,k])=>({name:n,x:t,y:T.map(x=>x.actual_controls?.[k])}))},'wb-capture-plot-surfaces'),
        plotCard({title:'Thrust (actual)',xlabel:'time from tube exit (s)',ylabel:'lbf',height:210,series:[{name:'thrust',x:t,y:T.map(x=>x.actual_controls?.thrust_lbf)}]},'wb-capture-plot-thrust')));}
  else out.appendChild(card(null,el('p',{class:'wb-status',text:'Run a launch to capture: the controller damps the tip-off rates, recovers attitude, then captures level flight.'})));
  return [left,out];
}

// ---------------------------------------------------------------- station 11: load envelopes & export
function envClearVertex(){wb.env.vertexRequest=(wb.env.vertexRequest||0)+1;wb.env.vertex=null;wb.env.vertexPoint=null;}
async function envLoadStudies(){try{const r=await api('/studies');wb.env.studies=r.studies.filter(m=>['completed','cancelled','failed'].includes(m.status)&&m.progress.completed>0);}catch(e){showError('envelopes',e);}}
async function envBuild(id){if(wb.busy)return;showError('envelopes',null);envClearVertex();setBusy(true);try{wb.env.result=await api('/studies/'+id+'/envelopes',{});wb.env.study=id;envClearVertex();
  const ids=Object.keys(wb.env.result.envelopes.stations);if(!ids.includes(wb.env.station))wb.env.station=ids.includes('wing-root-left')?'wing-root-left':ids[0];render();}catch(e){showError('envelopes',e);}finally{setBusy(false);}}
async function envVertex(v){if(wb.busy)return;envClearVertex();const request=wb.env.vertexRequest,result=wb.env.result,study=wb.env.study;
  const current=()=>request===wb.env.vertexRequest&&result===wb.env.result&&study===wb.env.study;
  showError('envelopes',null);const box=document.getElementById('wb-env-vertex');if(box)box.textContent=`Replaying sample ${v.sample} to t = ${fmt(v.time_s)} s…`;
  try{const vertex=await api('/studies/'+study+'/vertex',{sample:v.sample,time_s:v.time_s});if(!current())return;wb.env.vertex=vertex;wb.env.vertexPoint=v;render();document.getElementById('wb-env-vertex')?.scrollIntoView({block:'nearest'});}catch(e){if(current())showError('envelopes',e);}}
function envelopesView(){if(!wb.env)wb.env={studies:null,result:null,study:null,station:null,pair:0,vertex:null};const E=wb.env;
  if(E.studies===null&&!E.loading){E.loading=true;envLoadStudies().then(()=>{E.loading=false;render();});}
  const pick=el('select',{id:'wb-env-study'},...(E.studies||[]).map(m=>el('option',{value:m.id,selected:m.id===E.study,text:`${m.created_utc} ${m.label||m.name} (${m.status}, ${m.progress.completed}/${m.count})`})));
  const left=el('div',{},card('Study',(E.studies&&E.studies.length)?el('div',{},pick,el('div',{class:'wb-actions'},el('button',{class:'wb-btn',id:'wb-env-build',onclick:()=>envBuild(pick.value)},'Build envelopes'))):el('p',{class:'wb-status',text:'No finished Monte Carlo study yet. Run one at station 10.'}),
      el('button',{class:'wb-btn wb-secondary',id:'wb-env-refresh',onclick:async()=>{await envLoadStudies();render();}},'Refresh study list'),
      el('div',{class:'wb-error',id:'wb-error-envelopes',role:'alert'}),
      el('p',{class:'wb-status',text:'Complete envelopes replay every sample and cover every recorded load tick. Building may take as long as a study run. Only retained governing points are plotted; interior points were reduced. Peaks from different instants are never combined.'})));
  const out=el('div',{id:'wb-result-envelopes'}),R=E.result;
  if(R){const env=R.envelopes,ids=Object.keys(env.stations),st=env.stations[E.station],pairs=st.pairs,pi=Math.min(E.pair,pairs.length-1),pair=pairs[pi],pts=R.points[E.station][pi];
    const sSel=el('select',{id:'wb-env-station',onchange:e=>{envClearVertex();E.station=e.target.value;E.pair=0;render();}},...ids.map(id=>el('option',{value:id,selected:id===E.station,text:id})));
    const pSel=el('select',{id:'wb-env-pair',onchange:e=>{envClearVertex();E.pair=Number(e.target.value);render();}},...pairs.map((p,k)=>el('option',{value:String(k),selected:k===pi,text:p.x+' vs '+p.y})));
    const hull=pair.vertices.concat(pair.vertices.length?[pair.vertices[0]]:[]);
    const plot=el('div',{id:'wb-env-plot'});plotLines(plot,{title:`${E.station}: ${pair.x} vs ${pair.y} (click a vertex)`,xlabel:pair.x,ylabel:pair.y,height:320,series:[
      {name:'retained governing points',scatter:true,x:pts.points.map(q=>q.x),y:pts.points.map(q=>q.y),labels:pts.points.map(q=>`sample ${q.sample}, t = ${fmt(q.time_s)} s`)},
      {name:'hull',x:hull.map(v=>v.x),y:hull.map(v=>v.y)},
      {name:'vertices',scatter:true,x:pair.vertices.map(v=>v.x),y:pair.vertices.map(v=>v.y),ids:pair.vertices.map((v,k)=>k),labels:pair.vertices.map(v=>`vertex: sample ${v.sample}, t = ${fmt(v.time_s)} s`),onclick:i=>envVertex(pair.vertices[i])}]});
    const V=E.vertex;
    out.append(card('Envelope',badge(st.weakest_evidence),el('p',{class:'wb-status',text:`${env.coverage}: ${env.samples} samples, ${env.points} recorded concurrent fields. ${env.convention}`}),
        el('div',{class:'wb-actions'},el('label',{class:'wb-field wb-wide'},'Station',sSel),el('label',{class:'wb-field wb-wide'},'Pair',pSel)),plot,
        table(['vertex','x','y','sample','time (s)',''],pair.vertices.map((v,k)=>[k,v.x,v.y,v.sample,v.time_s,'']),'wb-env-vertices'),
        el('div',{class:'wb-actions'},...pair.vertices.map((v,k)=>el('button',{class:'wb-btn wb-secondary','data-vertex':String(k),onclick:()=>envVertex(v)},'Open vertex '+k)))),
      card('Governing case at the selected vertex',el('div',{id:'wb-env-vertex'},V?el('div',{},
          el('p',{id:'wb-env-resolves',class:V.resolves?'wb-ok':'wb-bad',text:V.resolves?`Replayed sample ${V.sample}, tick ${V.tick}, at t = ${fmt(V.time_s)} s reproduces the retained concurrent field exactly; report hash verified.`:`Replayed field at t = ${fmt(V.time_s)} s does NOT match the retained concurrent field.`}),
          aeroIdentity(V.aero_dataset),el('p',{class:'wb-status',text:`Status ${V.status}; values: ${Object.entries(V.values||{}).map(([k,x])=>k+' = '+fmt(x)).join(', ')}.`}),
          table(['station','Fx','Fy','Fz','Mx','My','Mz'],Object.entries(V.replayed_field).map(([id,f])=>[id,...f.force_B_lbf,...f.moment_B_in_lbf]),'wb-env-field')):el('p',{class:'wb-status',text:'Click a hull vertex to replay its governing run at that instant.'}))),
      card('Extremes (each from one concurrent instant)',table(['component','value','sample','time (s)'],Object.entries(st.extremes).map(([k,x])=>[k,x.value,x.sample,x.time_s]),'wb-env-extremes')),
      card('Export',el('div',{class:'wb-actions'},el('a',{href:`/api/workbench/studies/${E.study}/envelopes.csv`,id:'wb-env-csv',class:'wb-btn wb-secondary'},'Load table (CSV)'),el('a',{href:`/api/workbench/studies/${E.study}/envelopes.json`,id:'wb-env-json',class:'wb-btn wb-secondary'},'Envelopes (JSON)')),
        el('p',{class:'wb-status',text:'One row per hull vertex, with its governing sample and instant and evidence level; lbf and in-lbf, reactions on the retained part, body axes.'})));}
  else out.appendChild(card(null,el('p',{class:'wb-status',text:'Build envelopes from a finished Monte Carlo study.'})));
  return [left,out];
}

// ---------------------------------------------------------------- page
const VIEWS={sections:sectionsView,'aero-3d':aero3dView,dataset:datasetView,environment:environmentView,propulsion:propulsionView,plant:plantView,launch:launchView,capture:captureView,loads:loadsView,'monte-carlo':monteCarloView,envelopes:envelopesView};
function render(){
  const nav=document.getElementById('wb-nav');nav.textContent='';
  for(const s of wb.stations)nav.appendChild(el('button',{id:'wb-nav-'+s.id,disabled:!s.available,'aria-current':String(s.id===wb.current),onclick:()=>{stopAllPlayback();wb.current=s.id;try{localStorage.setItem('wb-station',s.id);}catch{}render();}},
    el('span',{class:'wb-num',text:String(s.number)}),s.title,s.available?null:el('span',{class:'wb-planned',text:'planned — #'+s.issue})));
  const main=document.getElementById('wb-main');main.textContent='';const s=wb.stations.find(x=>x.id===wb.current);if(!s)return;
  main.appendChild(el('h2',{id:'wb-title'},`Station ${s.number}: ${s.title}`));
  const [left,out]=VIEWS[s.id]();const grid=el('div',{class:'wb-grid2'},left,el('div',{},out));main.appendChild(grid);
  notesCard(s).then(c=>main.appendChild(c));refreshSaved(s.id);if(s.id==='plant')drawRunPlots();if(s.id==='loads')drawLoadHistory();
}
async function init(){try{const r=await api('/stations');wb.stations=r.stations;wb.defaults=r.defaults;wb.inputs=clone(r.defaults);
  let saved=null;try{saved=localStorage.getItem('wb-station');}catch{}wb.current=wb.stations.some(s=>s.id===saved&&s.available)?saved:wb.stations.find(s=>s.available).id;stopAllPlayback();render();}
  catch(e){document.getElementById('wb-main').textContent='Workbench unavailable: '+e.message;}}
init();
