"use strict";
(() => {
  const strategy=s=>s==="round_robin"?"RR 轮换":"LO 最少在途";
  const rate=n=>String(n);
  function prepare(run,endTime,single){return {width:876,height:single?926:356};}
  // Recompose the M4 pose; never select a different route or infer request state.
  function pose(prepared,t,snapshot,original){
    const {height}=prepared,limit=3,n=Math.min(limit,original.servers.length),center=height/2;
    const gap=n===1?0:(height-(original.servers.length>limit?110:76))/n;
    const servers=original.servers.slice(0,limit).map((s,i)=>({...s,y:center+(i-(n-1)/2)*gap-(original.servers.length>limit?26:0)}));
    const map=new Map(servers.map(s=>[s.server_id,s])),entities=[],counts=new Map();
    original.entities.forEach(e=>{
      const s=map.get(e.server_id);if(!s)return;
      if(e.status==="waiting"){const count=counts.get(s.server_id)||0;if(count>=3)return;counts.set(s.server_id,count+1);}
      let x,y=s.y;
      if(e.x<=230){x=52+(e.x-48)/(230-48)*(252-52);const source=original.servers.find(s=>s.server_id===e.server_id);
        y=Math.abs(source.y-85)>1e-8?center+(s.y-center)*(e.y-85)/(source.y-85):center;
      }else if(e.x<376)x=252+(e.x-230)/146*114;
      else if(e.x<=422)x=462+(e.x-422)*48/23;
      else x=462+(e.x-422)/93*156;
      entities.push({...e,x,y});
    });
    servers.forEach(s=>{s.queue_visible=entities.filter(e=>e.server_id===s.server_id&&e.status==="waiting").length;s.queue_hidden=s.queue_count-s.queue_visible;});
    const hidden=original.servers.slice(limit);
    const token_groups=[];
    for(const e of entities){
      if(e.status==="processing"&&!e.moving)continue;
      const group=token_groups.find(g=>Math.abs(g.x-e.x)<38&&Math.abs(g.y-e.y)<38);
      if(group)group.entities.push(e);else token_groups.push({x:e.x,y:e.y,entities:[e]});
    }
    return {...original,width:876,height,servers,entities,token_groups,hidden_servers:hidden,
      waiting_visible:entities.filter(e=>e.status==="waiting").length,
      waiting_hidden:original.waiting-entities.filter(e=>e.status==="waiting").length};
  }
  function draw(canvas,run,p,prepared,decision){
    const {ctx,text,box,line}=Sim2ClipVertical.painter(canvas,p.width,p.height),center=p.height/2;
    const rowHeight=p.servers.length===1?(p.height>400?320:166):Math.min(p.height>400?230:94,p.height/p.servers.length-18);
    text("请求入口",20,32,28,"#173b36");text("独立队列",400,32,28,"#173b36","center");text("处理槽",618,32,28,"#173b36","center");text("完成",805,32,28,"#117763","center");
    box(22,center-36,60,72,"#e7f2ec");text("→",52,center+13,40,"#117763","center");
    line([[82,center],[123,center]],"#91afa0",5);box(123,center-56,108,112,"#173b36","#173b36");
    // Keep labels clear of the real request lane through the splitter center.
    text("LB",177,center-23,40,"white","center",700);text(run.config.routing_strategy==="round_robin"?"轮换":"最少在途",177,center+44,22,"#d3e3d8","center");
    text(`未到达 ${p.not_arrived}`,26,p.height-14,26);
    p.servers.forEach(s=>{
      const y=s.y,chosen=s.server_id===decision?.server_id,h=rowHeight;
      line([[231,center],[252,center],[252,y],[472,y]],chosen?"#117763":"#b9cfc2",chosen?5:3);
      box(280,y-h/2,574,h,"white");
      text(`S${s.server_id}`,298,y-h/2+31,32,"#173b36","left",700,220);
      text(`${rate(s.rate)} work/s`,835,y-h/2+28,24,"#677972","right",500,300);
      line([[280,y+8],[556,y+8]],chosen?"#117763":"#cbd7cf",3);
      for(let i=0;i<3;i++)box(443-i*48,y-9,36,36,"#fcf8ee","#eadbbb",7);
      box(563,y-13,110,48,s.active_request!==null?"#e7f0f7":"#f0f2e9",s.active_request!==null?"#478bb4":"#dfe5dd",8);
      text(s.active_request===null?"空闲":`#${s.active_request}`,618,y+20,28,s.active_request!==null?"#306f98":"#677972","center",650,100);
      if(s.progress>0)box(568,y+31,100*s.progress,4,"#478bb4",null,2);
      line([[678,y+8],[744,y+8]],"#91afa0",4);text("→",710,y+18,30,"#117763","center");
      box(753,y-10,86,46,"#e7f2ec",null,8);text(String(s.completed),796,y+23,32,"#117763","center",700);
      text(`等 ${s.queue_count}${s.queue_hidden?` · 另 ${s.queue_hidden}`:""}`,298,y+h/2-4,h>110?30:26,"#936019");
      if(chosen){ctx.fillStyle="#117763";ctx.beginPath();ctx.arc(264,y,6,0,Math.PI*2);ctx.fill();}
    });
    // Exact overlap counts for concurrent route representatives, never random tokens.
    for(const group of p.token_groups){const list=group.entities;
      const e=list[0],mixed=list.some(x=>x.status!==e.status);box(e.x-18,e.y-18,36,36,e.status==="waiting"?"#e99b34":"#478bb4","white",7);
      if(mixed){ctx.fillStyle="#478bb4";ctx.fillRect(e.x,e.y-15,15,30);}
      if(list.length>1||String(e.request_id).length<=3)text(list.length>1?`×${list.length}`:String(e.request_id),e.x,e.y+7,list.length>1?20:18,"white","center",700);
    }
    if(p.hidden_servers.length){
      const hidden=p.hidden_servers,waiting=hidden.reduce((n,s)=>n+s.queue_count,0),busy=hidden.filter(s=>s.active_request!==null).length,done=hidden.reduce((n,s)=>n+s.completed,0);
      box(270,p.height-44,584,38,"#e7f2ec",null,7);text(`另 ${hidden.length} 台：等待 ${waiting} · 处理 ${busy} · 完成 ${done}`,562,p.height-16,24,"#173b36","center");
    }
  }
  window.Sim2ClipServerVertical={processingLabel:"处理",prepare,pose,draw,total:r=>r.config.requests,
    publicQuestionApplies:(runs,result)=>!result.request||runs.every((r,i)=>{const c=result.request.experiments[i].config;return r.config.routing_strategy===c.routing_strategy&&JSON.stringify(r.config.servers)===JSON.stringify(c.servers);}),
    presentationFacts:(runs,result,metric)=>{const a=runs[0].config,b=runs[1]?.config,pool=b&&JSON.stringify(a.servers)!==JSON.stringify(b.servers),rates=c=>[...new Set(c.servers.map(s=>s.processing_rate))].join(" / "),primary=metric||result.primary_metric||"average_response_time";
      const variables=!b?"单组，无对照变化项":pool?`服务器池 ${a.servers.length} → ${b.servers.length} 台；速率 ${rates(a)} → ${rates(b)} work/s`:`分流策略 ${strategy(a.routing_strategy)} → ${strategy(b.routing_strategy)}`;
      return {question:!b?`${a.requests} 个请求，${a.servers.length} 台服务器，响应多久？`:pool?`${a.requests} 个请求，不同服务器池，响应差多少？`:`${a.requests} 个请求，RR 与 LO 怎样改变响应？`,
        scene:"请求分流 · 每台服务器独立 FIFO",conditions:!b?`单组 · ${a.servers.length} 台 · ${strategy(a.routing_strategy)}`:pool?`A · ${a.servers.length} 台    B · ${b.servers.length} 台`:`A · ${strategy(a.routing_strategy)}    B · ${strategy(b.routing_strategy)}`,
        assumptions:`配置：${a.arrivals.mode==="batch"?"批量到达":`指数到达均值 ${a.arrivals.mean_interarrival_time} 秒`} · ${a.workload.mode==="constant"?`固定工作量 ${a.workload.work}`:`指数工作量均值 ${a.workload.mean_work}`}；速率 ${rates(a)} work/s${runs[0].workload_source==="supplied"?"；回放以保存样本为准":""}`,
        goal:!b?"观察单次服务器请求表现":`按已验证指标比较${pool?"服务器池":"分流策略"}`,
        controls:!b?"已保存的请求样本、独立 FIFO":pool?"同一批请求、分流策略、独立 FIFO":"同一批请求、服务器池、独立 FIFO",variables,
        primary,secondary:["p95_response_time","average_wait_time","max_queue_length","simulation_end_time"],checks:"复核请求与事件、路由 / 独立 FIFO、指标与共享样本"};},
    resultName:r=>`${r.config.servers.length} 台 · ${strategy(r.config.routing_strategy)}`,
    metricText:(r,k)=>k==="max_queue_length"?`${r.metrics[k]} 请求`:`${r.metrics[k]>=1e6?r.metrics[k].toExponential(3):r.metrics[k].toFixed(4)} 秒`,
    deltaText:(d,k)=>k==="max_queue_length"?`${d>0?"+":""}${d} 请求`:`${d>0?"+":""}${d.toFixed(4)} 秒`,
    name:r=>`${r.config.servers.length} 台服务器`,config:r=>`${strategy(r.config.routing_strategy)} · 每台独立 FIFO`,
    describe:runs=>{
      const a=runs[0].config,b=runs[1]?.config,pool=b&&JSON.stringify(a.servers)!==JSON.stringify(b.servers);
      const ratesChanged=b&&a.servers.some((s,i)=>s.processing_rate!==b.servers[i]?.processing_rate);
      return {type:runs.length===1?"服务器 · 单组实验":pool?
        a.servers.length===b.servers.length&&!ratesChanged?"服务器 · 服务器池对比":"服务器 · 容量 / 速率对比":"服务器 · 分流策略对比",
        question:!b?`${a.requests} 个请求，\n${a.servers.length} 台服务器，怎样处理？`:pool?
          a.servers.length!==b.servers.length?`${a.requests} 个请求，\n${a.servers.length} 台和 ${b.servers.length} 台，谁处理更快？`:
          `${a.requests} 个请求，\n${ratesChanged?"不同处理速率":"不同服务器池"}，响应差多少？`:
          `${a.requests} 个请求，\nRR 与 LO 怎样改变队列？`,
        conditions:!b?strategy(a.routing_strategy):pool?`A · ${a.servers.length} 台    B · ${b.servers.length} 台    相同请求样本`:
          `A · ${strategy(a.routing_strategy)}    B · ${strategy(b.routing_strategy)}`};
    },
    aria:(r,p)=>`${r.config.servers.length} 台服务器，未到达 ${p.not_arrived}，等待 ${p.waiting}，处理 ${p.processing}，完成 ${p.completed}。${p.waiting_visible} 个等待实体，${p.waiting_hidden} 个等待聚合。`,
    clock:t=>`${t>=1e6?t.toExponential(3):t.toFixed(3)} 秒`};
})();
