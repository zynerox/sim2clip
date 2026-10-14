"use strict";
(() => {
  const payload = JSON.parse(document.getElementById("experiment-data").textContent);
  const require = (ok, message) => { if (!ok) throw Error(message); };
  const bits = new DataView(new ArrayBuffer(8));
  function ulp(value){bits.setFloat64(0,Math.abs(value));const exponent=(bits.getUint32(0)>>>20)&2047;return exponent===0?Number.MIN_VALUE:2**(exponent-1075);}
  const close = (a,b) => Number.isFinite(a) && Number.isFinite(b) && Math.abs(a-b)<=Math.max(1e-9,4*Math.max(ulp(a),ulp(b)));
  const equal = (a,b) => JSON.stringify(sorted(a))===JSON.stringify(sorted(b));
  function sorted(v) {if(Array.isArray(v))return v.map(sorted);if(v&&typeof v==="object")return Object.fromEntries(Object.keys(v).sort().map(k=>[k,sorted(v[k])]));return v;}
  function checkSchema(v,s,root) {
    if(s.$ref)return checkSchema(v,s.$ref.split("/").slice(1).reduce((x,k)=>x[k],root),root);
    const choices=s.oneOf||s.anyOf;
    if(choices){let matches=0;for(const choice of choices){try{checkSchema(v,choice,root);matches++;}catch(_){}}require(s.oneOf?matches===1:matches>0,"数据类型与服务器 Schema 不符");return;}
    if(Object.hasOwn(s,"const"))require(v===s.const,"不支持的场景或契约版本");
    if(s.enum)require(s.enum.includes(v),"非法枚举");
    if(s.type==="object"){
      require(v&&typeof v==="object"&&!Array.isArray(v),"应为对象");
      for(const k of s.required||[])require(Object.hasOwn(v,k),`缺少字段 ${k}`);
      for(const [k,item] of Object.entries(v)){require(s.properties?.[k]||s.additionalProperties!==false,`未知字段 ${k}`);if(s.properties?.[k])checkSchema(item,s.properties[k],root);}
    }else if(s.type==="array"){
      require(Array.isArray(v)&&v.length>=(s.minItems||0)&&v.length<=(s.maxItems??Infinity),"数组数量无效");v.forEach(item=>checkSchema(item,s.items,root));
    }else if(s.type==="number"||s.type==="integer")require(typeof v==="number"&&Number.isFinite(v)&&(s.type!=="integer"||Number.isInteger(v))&&v>=(s.minimum??-Infinity)&&v<=(s.maximum??Infinity)&&v>(s.exclusiveMinimum??-Infinity)&&v<(s.exclusiveMaximum??Infinity),"数值无效");
    else if(s.type==="string")require(typeof v==="string"&&v.length>=(s.minLength||0)&&v.length<=(s.maxLength??Infinity)&&(!s.pattern||new RegExp(s.pattern).test(v)),"字符串无效");
    else if(s.type==="null")require(v===null,"字段应为空");
  }
  // Python's sha256-json-v1 writes float fields as 1.0 and small exponents as e-07.
  const floatKeys=new Set(["arrival_time","work","processing_rate","mean_interarrival_time","mean_work"]);
  function canonical(v,key="") {
    if(Array.isArray(v))return `[${v.map(x=>canonical(x)).join(",")}]`;
    if(v&&typeof v==="object")return `{${Object.keys(v).sort().map(k=>`${canonical(k)}:${canonical(v[k],k)}`).join(",")}}`;
    if(typeof v==="number"&&floatKeys.has(key)){
      if(Object.is(v,-0))return "-0.0";
      if(v!==0&&Math.abs(v)<1e-4)return v.toExponential().replace(/e([+-])(\d+)$/,(_,sign,n)=>`e${sign}${n.padStart(2,"0")}`);
      return Number.isInteger(v)?`${v}.0`:String(v);
    }
    return JSON.stringify(v).replace(/[\u007f-\uffff]/g,ch=>`\\u${ch.charCodeAt(0).toString(16).padStart(4,"0")}`);
  }
  async function hash(value) {require(crypto.subtle,"浏览器不支持本地 SHA-256 校验");const bytes=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(typeof value==="string"?value:canonical(value)));return [...new Uint8Array(bytes)].map(x=>x.toString(16).padStart(2,"0")).join("");}
  // CPython Random integer seeding and MT19937; used only to certify generated workloads.
  class Random {
    constructor(hex){const keys=hex.match(/.{8}/g).reverse().map(x=>parseInt(x,16));while(keys.length>1&&keys.at(-1)===0)keys.pop();this.mt=new Uint32Array(624);this.mt[0]=19650218;for(let i=1;i<624;i++)this.mt[i]=(Math.imul(1812433253,this.mt[i-1]^(this.mt[i-1]>>>30))+i)>>>0;
      let i=1,j=0;for(let k=624;k;k--){this.mt[i]=((this.mt[i]^Math.imul(this.mt[i-1]^(this.mt[i-1]>>>30),1664525))+keys[j]+j)>>>0;if(++i>=624){this.mt[0]=this.mt[623];i=1;}if(++j>=keys.length)j=0;}
      for(let k=623;k;k--){this.mt[i]=((this.mt[i]^Math.imul(this.mt[i-1]^(this.mt[i-1]>>>30),1566083941))-i)>>>0;if(++i>=624){this.mt[0]=this.mt[623];i=1;}}this.mt[0]=0x80000000;this.index=624;
    }
    uint(){if(this.index>=624){for(let i=0;i<624;i++){const y=(this.mt[i]&0x80000000)|(this.mt[(i+1)%624]&0x7fffffff);this.mt[i]=this.mt[(i+397)%624]^(y>>>1)^((y&1)?0x9908b0df:0);}this.index=0;}let y=this.mt[this.index++];y^=y>>>11;y^=(y<<7)&0x9d2c5680;y^=(y<<15)&0xefc60000;y^=y>>>18;return y>>>0;}
    draw(mean){for(let i=0;i<128;i++){const u=((this.uint()>>>5)*67108864+(this.uint()>>>6))/9007199254740992;const value=-Math.log(1-u)*mean;if(value>0)return value;}throw Error("随机采样失败");}
  }
  async function verifyHashes(run){
    require(run.workload_hash===await hash({workload_version:"server-workload-1",samples:run.request_samples}),"工作负载哈希错误");
    require(run.result_id===await hash({contract:"server-lb-result-v1",config:run.config,workload_hash:run.workload_hash,workload_source:run.workload_source,metadata:run.metadata}),"结果 ID 错误");
    if(run.workload_source==="generated"){
      const c=run.config,a=new Random(await hash(`sim2clip-server-workload-v1:${c.seed}:arrival`)),w=new Random(await hash(`sim2clip-server-workload-v1:${c.seed}:work`));let arrival=c.arrivals.mode==="batch"?c.arrivals.arrival_time:0;
      run.request_samples.forEach((s,i)=>{if(i&&c.arrivals.mode!=="batch")arrival+=a.draw(c.arrivals.mean_interarrival_time);const work=c.workload.mode==="constant"?c.workload.work:w.draw(c.workload.mean_work);require(s.request_id===i&&close(s.arrival_time,arrival)&&close(s.work,work),"样本不符合声明的 seed");});
    }
  }
  function createReplay(run, verified=false){
    if(!verified)validateRun(run);
    const ids=run.config.servers.map(s=>s.server_id);let cursor,time,states,queues,heads,active,done,counts;
    function reset(){cursor=0;time=-1;states=new Map(run.request_samples.map(s=>[s.request_id,"not_arrived"]));queues=Object.fromEntries(ids.map(s=>[s,[]]));heads=Object.fromEntries(ids.map(s=>[s,0]));active=Object.fromEntries(ids.map(s=>[s,null]));done=Object.fromEntries(ids.map(s=>[s,0]));counts={not_arrived:run.config.requests,waiting:0,processing:0,completed:0};}
    function apply(e){const id=e.request_id,s=e.server_id;if(e.event_type==="request_arrived"){states.set(id,"arrived");counts.not_arrived--;}
      else if(e.event_type==="request_routed"){queues[s].push(id);states.set(id,"waiting");counts.waiting++;}
      else if(e.event_type==="request_started"){heads[s]++;active[s]=id;states.set(id,"processing");counts.waiting--;counts.processing++;}
      else{active[s]=null;done[s]++;states.set(id,"completed");counts.processing--;counts.completed++;}cursor++;
    }
    reset();return {seek(t,full=true){require(Number.isFinite(t)&&t>=0,"无效模拟时间");if(t<time)reset();while(cursor<run.events.length&&run.events[cursor].timestamp<=t)apply(run.events[cursor]);time=t;
      return {timestamp:t,event_cursor:cursor,request_states:full?Object.fromEntries(states):undefined,queues:Object.fromEntries(ids.map(s=>[s,queues[s].slice(heads[s],full?undefined:heads[s]+6)])),queue_counts:Object.fromEntries(ids.map(s=>[s,queues[s].length-heads[s]])),active:{...active},completed_per_server:{...done},...counts};}};
  }
  function validateRun(run){
    const root=payload.schemas.ServerSimulationResult;checkSchema(run,root,root);
    const c=run.config,ids=c.servers.map(s=>s.server_id),records=new Map(run.request_records.map(r=>[r.request_id,r])),samples=run.request_samples;
    require(ids.every((s,i)=>!i||s>ids[i-1]),"服务器 ID 顺序错误");
    require(samples.length===c.requests&&records.size===c.requests&&run.request_records.length===c.requests&&run.events.length===4*c.requests,"请求数量不完整");
    const q=Object.fromEntries(ids.map(s=>[s,[]])),head=Object.fromEntries(ids.map(s=>[s,0])),active=Object.fromEntries(ids.map(s=>[s,null])),done=Object.fromEntries(ids.map(s=>[s,0])),peaks=Object.fromEntries(ids.map(s=>[s,0]));
    let cursor=0,arrival=0,rr=0,total=0,peak=0,last=-1;
    const count=s=>q[s].length-head[s];
    function consume(kind,id,s,t){const e=run.events[cursor++];require(e&&e.sequence===cursor-1&&e.timestamp===t&&e.event_type===kind&&e.request_id===id&&e.server_id===s,"同刻顺序、FIFO 或分流错误");require(e.total_queue_length_after_event===total,"全局队列计数错误");if(s!==null)require(e.server_queue_length_after_event===count(s)&&e.server_active_after_event===(active[s]!==null?1:0),"节点计数错误");}
    while(cursor<run.events.length){const future=ids.filter(s=>active[s]!==null).map(s=>records.get(active[s]).processing_end_time);if(arrival<samples.length)future.push(samples[arrival].arrival_time);require(future.length,"额外事件");const t=Math.min(...future);require(t>=last,"时钟倒退");
      for(const s of ids)if(active[s]!==null&&records.get(active[s]).processing_end_time===t){const id=active[s];active[s]=null;done[s]++;consume("request_completed",id,s,t);}
      function start(s){const id=q[s][head[s]++];total--;active[s]=id;require(records.get(id).processing_start_time===t,"开始时间错误");consume("request_started",id,s,t);}
      for(const s of ids)if(active[s]===null&&count(s))start(s);
      while(arrival<samples.length&&samples[arrival].arrival_time===t){const sample=samples[arrival],id=sample.request_id,r=run.request_records[arrival];require(!arrival||samples[arrival-1].arrival_time<t||samples[arrival-1].request_id<id,"样本顺序错误");require(r&&r.request_id===id&&r.arrival_time===t&&r.dispatch_time===t&&r.work===sample.work,"样本与记录不一致");consume("request_arrived",id,null,t);
        let s;if(c.routing_strategy==="round_robin"){s=ids[rr];rr=(rr+1)%ids.length;}else{s=ids.reduce((best,node)=>count(node)+(active[node]!==null)<count(best)+(active[best]!==null)?node:best,ids[0]);}
        require(r.server_id===s,"路由目标错误");const rate=c.servers.find(node=>node.server_id===s).processing_rate;
        require(r.processing_end_time>r.processing_start_time&&r.processing_start_time>=t&&close(r.processing_time,sample.work/rate)&&close(r.processing_end_time,r.processing_start_time+r.processing_time)&&close(r.wait_time,r.processing_start_time-t)&&close(r.response_time,r.processing_end_time-t),"请求时间或速率错误");
        q[s].push(id);total++;consume("request_routed",id,s,t);if(active[s]===null)start(s);arrival++;
      }
      peak=Math.max(peak,total);ids.forEach(s=>peaks[s]=Math.max(peaks[s],count(s)));last=t;
    }
    require(arrival===c.requests&&total===0&&ids.every(s=>active[s]===null)&&Object.values(done).reduce((a,b)=>a+b,0)===c.requests,"请求状态不守恒");
    const responses=run.request_records.map(r=>r.response_time).sort((a,b)=>a-b),mean=key=>run.request_records.reduce((sum,r)=>sum+r[key],0)/c.requests,m=run.metrics;
    require(close(m.average_response_time,mean("response_time"))&&close(m.average_wait_time,mean("wait_time"))&&close(m.p95_response_time,responses[Math.ceil(.95*c.requests)-1])&&m.max_queue_length===peak&&m.completed_requests===c.requests&&m.simulation_end_time===last,"指标错误");
    require(m.per_server.length===ids.length&&m.per_server.every((p,i)=>p.server_id===ids[i]&&p.completed_requests===done[p.server_id]&&p.max_queue_length===peaks[p.server_id]),"节点指标错误");return run;
  }
  const metricNames=["average_response_time","p95_response_time","average_wait_time","max_queue_length","simulation_end_time"];
  async function validateEvidence(result){
    require(result.scenario==="server_load_balancing","此页面只支持 server_load_balancing 场景");
    const comparison=result.result_kind==="comparison",root=payload.schemas[comparison?"ServerComparisonResult":"ServerSimulationResult"];checkSchema(result,root,root);
    const runs=comparison?result.results.map(x=>x.result):[result];require(runs.reduce((n,r)=>n+r.config.requests,0)<=100000,"展示预算超过 100000 request-runs");
    for(const run of runs){validateRun(run);await verifyHashes(run);}
    if(comparison){const req=result.request,groups=req.experiments.map(e=>e.group_id),seeds=req.seeds;
      require(new Set(groups).size===groups.length&&seeds.every((s,i)=>!i||s>seeds[i-1])&&equal(seeds,result.seeds)&&result.baseline_group_id===groups[0]&&runs.length===groups.length*seeds.length,"比较分组不完整");
      const base=req.experiments[0].config;
      require(new Set(req.experiments.map(e=>canonical(e.config))).size===groups.length,"重复实验组");
      for(const e of req.experiments){require(e.config.seed===seeds[0]&&equal([e.config.requests,e.config.arrivals,e.config.workload],[base.requests,base.arrivals,base.workload]),"工作负载设置不公平");require(req.comparison_axis==="routing_strategy"?equal(e.config.servers,base.servers):e.config.routing_strategy===base.routing_strategy,"比较轴配置不一致");}
      seeds.forEach((seed,si)=>groups.forEach((group,gi)=>{const wrapped=result.results[si*groups.length+gi],run=wrapped.result;require(wrapped.group_id===group&&equal(run.config,{...req.experiments[gi].config,seed}),"组别或 Seed 不一致");const a=runs[si*groups.length];require(run.workload_hash===a.workload_hash&&run.workload_source===a.workload_source&&equal(run.request_samples,a.request_samples),"配对组未共享样本");}));
      require(result.summaries.length===groups.length&&result.paired_differences.length===seeds.length*(groups.length-1),"汇总不完整");
      const means=[];groups.forEach((group,gi)=>{const s=result.summaries[gi],rs=runs.filter((_,i)=>i%groups.length===gi);require(s.group_id===group&&equal(s.result_ids,rs.map(r=>r.result_id)),"汇总引用错误");for(const k of metricNames){const values=rs.map(r=>r.metrics[k]);require(close(s[k].mean,values.reduce((a,b)=>a+b,0)/values.length)&&s[k].minimum===Math.min(...values)&&s[k].maximum===Math.max(...values),"汇总指标错误");}means.push(s.average_response_time.mean);});
      seeds.forEach((seed,si)=>groups.slice(1).forEach((group,gi)=>{const d=result.paired_differences[si*(groups.length-1)+gi],a=runs[si*groups.length],b=runs[si*groups.length+gi+1];require(d.seed===seed&&d.candidate_group_id===group&&d.baseline_result_id===a.result_id&&d.candidate_result_id===b.result_id,"差值引用错误");for(const k of metricNames)require(close(d[k+"_difference"],b.metrics[k]-a.metrics[k]),"配对差值错误");}));
      const rank=groups.map((g,i)=>({g,i})).sort((a,b)=>means[a.i]-means[b.i]||a.i-b.i),best=means[rank[0].i];require(equal(result.ranking_group_ids,rank.map(x=>x.g))&&equal(result.winner_group_ids,rank.filter(x=>Math.abs(means[x.i]-best)<=Math.max(1e-9,1e-9*Math.max(Math.abs(best),Math.abs(means[x.i])))).map(x=>x.g)),"比较排名错误");
      require(result.comparison_id===await hash({contract:"server-lb-comparison-v1",request:req,runs:result.results.map(x=>({group_id:x.group_id,seed:x.result.config.seed,result_id:x.result.result_id}))}),"比较 ID 错误");
    }return runs;
  }
  window.Sim2ClipServerReplay={createReplay,validateEvidence,validateRun,hash,payload,numericEqual:close};
})();
