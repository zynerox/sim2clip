"use strict";
(() => {
  const elements=Object.fromEntries([...document.querySelectorAll('[id]')].map(el=>[el.id,el]));
  const payload=Sim2ClipServerReplay.payload,$=id=>elements[id];
  const {createReplay,validateEvidence}=Sim2ClipServerReplay;
  const stage=new Sim2ClipVertical.Stage(Sim2ClipServerVertical);
  let result=null,runs=[],selected=[],replays=[],scenes=[],panels=[],decisions=[],poses=[],clock=0,endTime=0,playing=false,lastFrame=0,generation=0;
  let narrative;
  const node=(tag,text,cls)=>{const el=document.createElement(tag);el.textContent=text;if(cls)el.className=cls;return el;};
  const strategy=s=>s==="round_robin"?"Round Robin":"Least Outstanding";
  function pause(){playing=false;$("play").textContent="播放实验 ▶";}
  function clear(){pause();narrative?.clear();stage.clear();clock=0;endTime=0;selected=[];replays=[];scenes=[];decisions=[];poses=[];panels=[];$("panels").replaceChildren();$("seed").replaceChildren();$("candidate").replaceChildren();$("summary-table").replaceChildren();$("summary-note").textContent="";$("axis-note").textContent="";$("provenance").textContent="";$("timeline").replaceChildren();$("report").textContent="";$("clock").textContent="0.000";$("scrubber").value=0;$("play").disabled=true;}
  function makePanel(run,index){const panel=node("article","",`panel ${index?"b":"a"}`);panel.innerHTML='<div class="panel-head"><div class="panel-title"><span class="badge"></span><h3></h3></div><span class="tag run-status"></span></div><div class="params"></div><div class="stats"><div class="stat"><small>当前等待</small><strong class="waiting">0</strong></div><div class="stat"><small>当前处理</small><strong class="processing">0</strong></div><div class="stat"><small>当前完成</small><strong class="completed">0</strong></div></div><div class="scene-viewport"><canvas width="720" height="332" role="img"></canvas></div><div class="scene-accounting"></div><div class="route-decision"></div><div class="server-metrics"></div>';
    panel.querySelector(".badge").textContent=selected.length===1?"单组":index?"B":"A";const group=result.result_kind==="comparison"?(index?$("candidate").value:result.baseline_group_id):"single";
    panel.querySelector("h3").textContent=`${group} · ${strategy(run.config.routing_strategy)}`;
    const c=run.config;panel.querySelector(".params").textContent=`${c.requests} 请求 · ${c.servers.length} 台服务器 · seed ${c.seed} · 到达 ${JSON.stringify(c.arrivals)} · 工作量 ${JSON.stringify(c.workload)} · 来源 ${run.workload_source}${run.workload_source==="supplied"?"（以实际样本为准）":""} · workload ${run.workload_hash.slice(0,12)}…`;
    const metricLabels=[["average_response_time","平均响应 / s"],["p95_response_time","P95 nearest-rank / s"],["average_wait_time","平均等待 / s"],["max_queue_length","队列峰值 / 请求"],["simulation_end_time","全部完成时间 / s"]];
    const metrics=panel.querySelector(".server-metrics");const title=node("div","");title.append(node("small",`当前 Seed ${c.seed}`),node("strong","单次最终指标"));metrics.append(title);
    for(const [key,label] of metricLabels){const cell=node("div","");cell.append(node("small",label),node("strong",key==="max_queue_length"?String(run.metrics[key]):run.metrics[key].toFixed(4)));metrics.append(cell);}
    $("panels").append(panel);return panel;
  }
  function buildDecisions(run){const ids=run.config.servers.map(s=>s.server_id),counts=Object.fromEntries(ids.map(s=>[s,0])),decisions=[];let rr=0;
    for(const e of run.events){if(e.event_type==="request_routed"){decisions.push({timestamp:e.timestamp,request_id:e.request_id,server_id:e.server_id,before:ids.map(s=>counts[s]),cursor:rr,ids});counts[e.server_id]++;rr=(rr+1)%ids.length;}else if(e.event_type==="request_completed")counts[e.server_id]--;}
    return decisions;
  }
  function decisionAt(list,t){let lo=0,hi=list.length;while(lo<hi){const mid=(lo+hi)>>>1;if(list[mid].timestamp<=t)lo=mid+1;else hi=mid;}return lo?list[lo-1]:null;}
  function choose(){pause();const seed=Number($("seed").value);if(result.result_kind==="comparison")selected=[result.baseline_group_id,$("candidate").value].map(group=>result.results.find(x=>x.group_id===group&&x.result.config.seed===seed).result);else selected=[runs[0]];
    endTime=Math.max(...selected.map(r=>r.metrics.simulation_end_time));replays=selected.map(r=>createReplay(r,true));scenes=selected.map(r=>new Sim2ClipServerScene.ServerSceneTimeline(r));decisions=selected.map(buildDecisions);
    stage.configure(selected,endTime);
    $("panels").replaceChildren();$("panels").classList.toggle("single",selected.length===1);panels=selected.map(makePanel);$("end-time").textContent=`${endTime.toFixed(4)} 秒 · 选中组全部完成`;
    $("headline").textContent=result.result_kind!=="comparison"?`${selected[0].config.requests} 个请求，观察一次真实分流。`:result.request.comparison_axis==="server_pool"?`${selected[0].config.requests} 个请求，${selected[0].config.servers.length} 台 vs ${selected[1].config.servers.length} 台服务器。`:`同一批请求，Round Robin 与 Least Outstanding 怎样分流？`;
    seek(0);
    narrative.reset();
  }
  function seek(t){if(narrative?.mode==="presentation"&&!narrative.driving)return narrative.seekSimulation(t);if(!Number.isFinite(t)||t<0)throw Error("无效模拟时间");clock=Math.min(endTime,t);$("clock").textContent=clock.toFixed(3);$("scrubber").value=endTime?Math.round(clock/endTime*1000):0;
    const snapshots=replays.map(r=>r.seek(clock,false));poses=snapshots.map((s,i)=>scenes[i].seek(clock,s));
    snapshots.forEach((s,i)=>{const panel=panels[i],run=selected[i],pose=poses[i],d=decisionAt(decisions[i],clock);for(const key of ["waiting","processing","completed"])panel.querySelector("."+key).textContent=s[key];panel.querySelector(".run-status").textContent=s.completed===run.config.requests?"全部完成":"事件回放中";
      const canvas=panel.querySelector("canvas");Sim2ClipServerScene.draw(canvas,run,pose,d);canvas.setAttribute("aria-label",`${run.config.requests} 请求：等待 ${s.waiting}，处理 ${s.processing}，完成 ${s.completed}。每节点独立 FIFO。`);
      panel.querySelector(".scene-accounting").textContent=`未到达 ${s.not_arrived} · 等待 ${pose.servers.reduce((n,x)=>n+x.queue_visible,0)} 个实体 + ${pose.servers.reduce((n,x)=>n+x.queue_hidden,0)} 个聚合请求 · 处理 ${s.processing} · 完成 ${s.completed} · 全部 ${run.config.requests} 请求守恒`;
      const target=panel.querySelector(".route-decision");target.replaceChildren();
      if(d){target.append(node("div",`最近实际路由：#${d.request_id} → S${d.server_id}，t=${d.timestamp.toFixed(4)}s`));target.append(node("small",`分流前在途（等待 + 处理）：${d.ids.map((id,j)=>`S${id}=${d.before[j]}`).join(" · ")}；${run.config.routing_strategy==="round_robin"?`RR 游标 ${d.cursor} → ${(d.cursor+1)%d.ids.length}，按升序服务器 ID 轮换。`:"LO 选择在途最少的节点，平局选最小 ID；不看速率或剩余工作量。"}`));}else target.append(node("div","尚无请求到达，等待真实路由事件。"));
    });stage.update(snapshots,poses,clock,endTime,decisions.map(list=>decisionAt(list,clock)));if(clock>=endTime&&narrative?.mode!=="presentation")pause();return snapshots;
  }
  function renderSummary(){const comp=result.result_kind==="comparison";$("summary-note").textContent=comp?`全部 ${result.seeds.length} 个配对 Seed：${result.seeds.join(", ")}。以下为每次实验指标的种子间均值 [最小, 最大]。P95 列为每次实验 P95 的均值，不是合并请求后的 P95。差值方向为 candidate − baseline；数值排名不代表统计显著性。`:"单组模式：上方仅显示当前 Seed 的单次最终指标，没有构造 A/B 或多 Seed 汇总。";
    if(!comp)return;
    const table=node("table",""),head=node("tr","");for(const text of ["实验组","平均响应 / s","每次 P95 均值 / s","平均等待 / s","队列峰值均值","完成时间均值 / s","配对响应差均值 / s"])head.append(node("th",text));const th=node("thead","");th.append(head);table.append(th);const body=node("tbody","");
    for(const s of result.summaries){const row=node("tr","");row.append(node("td",s.group_id));for(const key of ["average_response_time","p95_response_time","average_wait_time","max_queue_length","simulation_end_time"]){const m=s[key];row.append(node("td",`${m.mean.toFixed(3)} [${m.minimum.toFixed(3)}, ${m.maximum.toFixed(3)}]`));}const ds=result.paired_differences.filter(d=>d.candidate_group_id===s.group_id);row.append(node("td",ds.length?(ds.reduce((n,d)=>n+d.average_response_time_difference,0)/ds.length).toFixed(4):"baseline"));body.append(row);}table.append(body);$("summary-table").append(table);
    $("axis-note").textContent=result.request.comparison_axis==="server_pool"?"容量/服务器池实验：策略固定，服务器数量或速率发生变化。结果差异包含服务器池能力变化，不能解释为策略单独造成。":"策略实验：服务器池、到达和工作量相同。RR 按数量轮换；LO 按在途请求数选择。异构速率也会影响实际处理时长，两种策略均不保证获胜。";
  }
  async function loadEvidence(value,meta={}){const token=++generation;clear();result=null;runs=[];$("error").hidden=true;
    try{const validated=await validateEvidence(value);if(token!==generation)return;result=value;runs=validated;
      const seeds=result.result_kind==="comparison"?result.seeds:[result.config.seed];for(const seed of seeds)$("seed").append(new Option(String(seed),String(seed)));if(seeds.includes(42))$("seed").value="42";
      const groups=result.result_kind==="comparison"?result.request.experiments.map(e=>e.group_id):[];for(const group of groups.slice(1))$("candidate").append(new Option(group,group));$("candidate-label").hidden=groups.length<2;
      $("provenance").textContent=`server_load_balancing · schema ${result.schema_version} · ${(result.comparison_id||result.result_id).slice(0,16)}… · SHA-256 已核对`;
      $("goal").textContent=meta.context?.experiment_goal||"每台服务器独立 FIFO、一个处理槽。追踪路由决策与真实请求生命周期。";
      $("report").textContent=meta.context?.report||"仅加载模拟结果，未提供 Agent 报告。服务器场景当前不支持 GIF 导出。";
      const timeline=meta.agent_timeline||[];if(!timeline.length)$("timeline").append(node("li","此结果没有 Agent 执行记录。"));else for(const item of timeline){const li=node("li","");li.append(node("strong",item.name),node("time",item.timestamp||"时间未记录"),node("p",`${item.status} · R${item.round} · ${item.reason||item.description||item.detail||""}`));for(const call of item.tool_calls||[]){li.append(node("p",`${call.tool_name||call.name||"MCP"} · ${call.call_id||call.id||""} · ${call.status||""}`));}$("timeline").append(li);}
      narrative.load(meta.presentation);renderSummary();choose();$("play").disabled=false;
      if(meta.presentation?.config.default_mode==="presentation")narrative.setMode("presentation");
    }catch(error){if(token===generation){clear();$("error").textContent=error.message;$("error").hidden=false;}throw error;}
  }
  function restorePlaying(value){playing=value;lastFrame=performance.now();$("play").textContent=value?"暂停回放 Ⅱ":"播放实验 ▶";}
  function play(){if(!selected.length)return;if(narrative.mode==="presentation"){if(narrative.p>=narrative.config.duration_seconds)narrative.seek(0);}else if(clock>=endTime)seek(0);restorePlaying(true);}
  function tick(now){if(playing){const dt=Math.max(0,Math.min((now-lastFrame)/1000,.25))*Number($("speed").value);if(narrative.mode==="presentation")narrative.advance(dt);else seek(clock+dt*endTime/30);lastFrame=now;}requestAnimationFrame(tick);}
  narrative=new Sim2ClipPresentation.Controller(stage,Sim2ClipServerVertical,{get:()=>({result,runs:selected,endTime,clock,playing}),seek,pause,restorePlaying});
  $("play").onclick=()=>playing?pause():play();$("restart").onclick=()=>{pause();if(narrative.mode==="presentation")narrative.seek(0);else seek(0);};$("scrubber").oninput=()=>{pause();const ratio=Number($("scrubber").value)/1000;if(narrative.mode==="presentation")narrative.seek(ratio*narrative.config.duration_seconds);else seek(ratio*endTime);};$("seed").onchange=choose;$("candidate").onchange=choose;
  $("load-file").onchange=async()=>{const file=$("load-file").files[0];if(!file)return;const token=++generation;clear();result=null;runs=[];try{const raw=await file.text();if(token===generation)await loadEvidence(JSON.parse(raw));}catch(error){if(token===generation){clear();$("error").textContent=error.message;$("error").hidden=false;}}finally{$("load-file").value="";}};
  $("download").onclick=()=>{if(!result)return;const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:"application/json"})),a=document.createElement("a");a.href=url;a.download="result.json";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  const setLayout=layout=>{stage.setLayout(layout);narrative.layoutChanged();};$("layout").onchange=e=>setLayout(e.target.value);
  window.Sim2Clip={scenario_id:"server_load_balancing",createReplay,validateEvidence,createScene:run=>new Sim2ClipServerScene.ServerSceneTimeline(run),loadEvidence,seek,pause,play,getScenePoses:()=>poses,getStagePoses:()=>stage.poses,setLayout,getLayout:()=>stage.layout,setMode:mode=>narrative.setMode(mode),seekPresentation:p=>narrative.seek(p),getPresentationState:()=>narrative.state(),getState:()=>({clock,endTime,playing,selected:selected.map(r=>r.result_id),snapshots:replays.map(r=>r.seek(clock))}),getResult:()=>result};
  window.Sim2Clip.ready=loadEvidence(payload.result,payload).catch(()=>{});requestAnimationFrame(tick);
})();
