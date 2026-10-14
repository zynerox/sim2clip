"use strict";
(() => {
  const payload = JSON.parse(document.getElementById("experiment-data").textContent);
  const close = (a,b) => Number.isFinite(a)&&Number.isFinite(b)&&Math.abs(a-b)<=Math.max(1e-9,1e-9*Math.max(Math.abs(a),Math.abs(b)));
  // Queue v1 canonical JSON. Float fields retain Python's 0.0/exponent spelling.
  const floatKeys=new Set(["arrival_time","service_time","mean_interarrival_time","mean_service_time"]);
  function canonical(v,key="",normalizeZero=false) {
    if(Array.isArray(v))return `[${v.map(x=>canonical(x,"",normalizeZero)).join(",")}]`;
    if(v&&typeof v==="object")return `{${Object.keys(v).sort().map(k=>`${canonical(k)}:${canonical(v[k],k,normalizeZero)}`).join(",")}}`;
    if(typeof v==="number"&&floatKeys.has(key)) {
      if(Object.is(v,-0))return normalizeZero?"0.0":"-0.0";
      if(v!==0&&(Math.abs(v)<1e-4||Math.abs(v)>=1e16))return v.toExponential().replace(/e([+-])(\d+)$/,(_,sign,n)=>`e${sign}${n.padStart(2,"0")}`);
      return Number.isInteger(v)?`${v}.0`:String(v);
    }
    return JSON.stringify(v).replace(/[\u007f-\uffff]/g,ch=>`\\u${ch.charCodeAt(0).toString(16).padStart(4,"0")}`);
  }
  // Synchronous SHA-256 keeps the existing Queue loadEvidence API synchronous.
  // Its input is canonical ASCII; no external resources or async startup race.
  const K=[0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2];
  const rotate=(x,n)=>(x>>>n)|(x<<(32-n));
  function digest(value,normalizeZero=false) {
    const text=canonical(value,"",normalizeZero), size=Math.ceil((text.length+9)/64)*64,
      bytes=new Uint8Array(size), view=new DataView(bytes.buffer), w=new Uint32Array(64),
      h=new Uint32Array([0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19]);
    for(let i=0;i<text.length;i++)bytes[i]=text.charCodeAt(i);
    bytes[text.length]=128;view.setUint32(size-8,Math.floor(text.length/0x20000000));view.setUint32(size-4,text.length*8);
    for(let offset=0;offset<size;offset+=64) {
      for(let i=0;i<16;i++)w[i]=view.getUint32(offset+i*4);
      for(let i=16;i<64;i++) {const a=w[i-15],b=w[i-2];w[i]=w[i-16]+(rotate(a,7)^rotate(a,18)^(a>>>3))+w[i-7]+(rotate(b,17)^rotate(b,19)^(b>>>10));}
      let [a,b,c,d,e,f,g,j]=h;
      for(let i=0;i<64;i++) {const t1=(j+(rotate(e,6)^rotate(e,11)^rotate(e,25))+((e&f)^((~e)&g))+K[i]+w[i])>>>0,
        t2=((rotate(a,2)^rotate(a,13)^rotate(a,22))+((a&b)^(a&c)^(b&c)))>>>0;
        j=g;g=f;f=e;e=(d+t1)>>>0;d=c;c=b;b=a;a=(t1+t2)>>>0;}
      [a,b,c,d,e,f,g,j].forEach((v,i)=>{h[i]=(h[i]+v)>>>0;});
    }
    return [...h].map(x=>x.toString(16).padStart(8,"0")).join("");
  }
  function checkSchema(value, schema, root) {
    if (schema.$ref) return checkSchema(value, schema.$ref.split("/").slice(1).reduce((a,k)=>a[k],root),root);
    if (schema.anyOf) {
      for (const option of schema.anyOf) {try {checkSchema(value,option,root); return;} catch (_) {}}
      throw Error("数据类型与共享 Schema 不符");
    }
    if (Object.hasOwn(schema,"const") && value!==schema.const) throw Error("不支持的数据契约版本或场景");
    if (schema.enum && !schema.enum.includes(value)) throw Error("未知事件或配置枚举");
    const type = schema.type;
    if (type === "object") {
      if (!value || typeof value!=="object" || Array.isArray(value)) throw Error("应为 JSON 对象");
      for (const key of schema.required || []) if (!Object.hasOwn(value,key)) throw Error(`缺失必需字段：${key}`);
      for (const [key,item] of Object.entries(value)) {
        if (!schema.properties?.[key] && schema.additionalProperties===false) throw Error(`未知字段：${key}`);
        if (schema.properties?.[key]) checkSchema(item,schema.properties[key],root);
      }
    } else if (type === "array") {
      if (!Array.isArray(value) || value.length<(schema.minItems||0) || value.length>(schema.maxItems||Infinity)) throw Error("数组数量无效");
      value.forEach(item=>checkSchema(item,schema.items,root));
    } else if (type === "number" || type === "integer") {
      if (typeof value!=="number" || !Number.isFinite(value) || (type==="integer"&&!Number.isInteger(value)) ||
          value<(schema.minimum??-Infinity) || value>(schema.maximum??Infinity) ||
          value<=(schema.exclusiveMinimum??-Infinity)) throw Error("数值无效");
    } else if (type === "string") {
      if (typeof value!=="string" || (schema.pattern&&!new RegExp(schema.pattern).test(value))) throw Error("字符串字段无效");
    } else if (type === "null" && value!==null) throw Error("字段应为空");
  }
  function createReplay(run) {
    const samples = new Map(run.customer_samples.map(s=>[s.customer_id,s]));
    const records = new Map(run.customer_records.map(r=>[r.customer_id,r]));
    if (samples.size!==run.config.customers || records.size!==samples.size ||
        run.events.length!==samples.size*3 || run.metrics.completed_customers!==samples.size)
      throw Error("事件缺失：需要完整的顾客生命周期数据");
    let cursor, timestamp, states, queue, cashiers, completed;
    function reset() {cursor=0;timestamp=-1;states=new Map([...samples.keys()].map(id=>[id,"not_arrived"]));queue=[];
      cashiers=Array(run.config.cashiers).fill(null);completed=0;}
    function apply(e) {
      const id=e.customer_id, slot=e.cashier_id===null?null:e.cashier_id-1;
      if (!states.has(id) || (slot!==null&&(slot<0||slot>=cashiers.length))) throw Error("事件引用未知顾客或收银台");
      if (e.event_type==="customer_arrived") {
        if (states.get(id)!=="not_arrived" || slot!==null) throw Error("重复到达或非法收银台");
        states.set(id,"waiting");queue.push(id);
      } else if (e.event_type==="customer_started") {
        if (states.get(id)!=="waiting" || queue[0]!==id || slot===null || cashiers[slot]!==null) throw Error("FIFO 或收银台占用错误");
        queue.shift();states.set(id,"serving");cashiers[slot]=id;
      } else if (e.event_type==="customer_finished") {
        if (states.get(id)!=="serving" || slot===null || cashiers[slot]!==id) throw Error("服务生命周期不完整");
        states.set(id,"completed");cashiers[slot]=null;completed++;
      } else throw Error("未知事件类型");
      if (queue.length!==e.queue_length_after_event) throw Error("队列长度与事件不一致");
      cursor++;
    }
    reset();let peak=0, previous=-1;
    run.events.forEach((e,index)=> {
      const sample=samples.get(e.customer_id), record=records.get(e.customer_id);
      if (e.sequence!==index || e.timestamp<previous || !sample || !record) throw Error("事件顺序或顾客记录无效");
      const expected=e.event_type==="customer_arrived"?sample.arrival_time:e.event_type==="customer_started"?record.service_start_time:record.service_end_time;
      if (!close(e.timestamp,expected) || (e.cashier_id!==null&&e.cashier_id!==record.cashier_id)) throw Error("事件与顾客记录不一致");
      if (!close(record.arrival_time,sample.arrival_time)||!close(record.service_time,sample.service_time)||
          !close(record.service_end_time-record.service_start_time,sample.service_time)||
          !close(record.wait_time,record.service_start_time-sample.arrival_time)||record.wait_time<0)
        throw Error("顾客记录时间不一致");
      apply(e);previous=e.timestamp;
      if (index===run.events.length-1 || run.events[index+1].timestamp!==e.timestamp) peak=Math.max(peak,queue.length);
    });
    const averages=Array.from(records.values());
    if (completed!==samples.size || peak!==run.metrics.max_queue_length ||
        !close(previous,run.metrics.simulation_end_time) ||
        !close(averages.reduce((n,r)=>n+r.wait_time,0)/samples.size,run.metrics.average_wait_time) ||
        !close(averages.reduce((n,r)=>n+r.service_time,0)/samples.size,run.metrics.average_service_time) ||
        !close(Math.max(...averages.map(r=>r.wait_time)),run.metrics.max_wait_time)) throw Error("指标与事件/记录不一致");
    reset();
    return {seek(t) {
      if (!Number.isFinite(t)||t<0) throw Error("无效模拟时间");
      if(t<timestamp)reset();while(cursor<run.events.length&&run.events[cursor].timestamp<=t)apply(run.events[cursor]);timestamp=t;
      return {timestamp:t,events_applied:cursor,customer_states:Object.fromEntries(states),queue:[...queue],cashiers:[...cashiers],completed};
    }};
  }
  function validateEvidence(result) {
    const schema=payload.schemas[result.comparison_id?"ComparisonResult":"SimulationResult"];
    checkSchema(result,schema,schema);
    const runs=result.results||[result];
    if (runs.reduce((n,r)=>n+r.config.customers,0)>100000) throw Error("结果超过展示预算");
    runs.forEach(createReplay);
    for(const run of runs) {
      const samples=[...run.customer_samples].sort((a,b)=>a.arrival_time-b.arrival_time||a.customer_id-b.customer_id);
      // Some host JS transports serialize positive float zero as negative zero.
      if((digest(samples)!==run.workload_hash&&digest(samples,true)!==run.workload_hash)||digest({config:run.config,workload_hash:run.workload_hash,workload_source:run.workload_source,metadata:run.metadata})!==run.result_id)throw Error("顾客样本哈希或结果来源不一致");
    }
    if(result.comparison_id) {
      if(digest({request:result.request,result_ids:runs.map(r=>r.result_id)})!==result.comparison_id)throw Error("比较哈希与结果引用不一致");
      const groups=result.request.experiments.map(c=>c.cashiers), seeds=result.request.seeds;
      if(new Set(groups).size!==groups.length || new Set(seeds).size!==seeds.length ||
         JSON.stringify(seeds)!==JSON.stringify(result.seeds) || runs.length!==groups.length*seeds.length)throw Error("实验分组不完整");
      for(const seed of seeds) {
        const paired=runs.filter(r=>r.config.seed===seed);
        if(paired.length!==groups.length||new Set(paired.map(r=>r.config.cashiers)).size!==groups.length)throw Error("配对实验缺失");
        for(const run of paired) {
          const expected=result.request.experiments.find(c=>c.cashiers===run.config.cashiers);
          if(!expected||JSON.stringify({...expected,seed})!==JSON.stringify(run.config)) {
            // Compare by keys rather than object insertion order.
            if(!expected||Object.keys(expected).some(k=>k!=="seed"&&expected[k]!==run.config[k]))throw Error("实验配置不匹配");
          }
          if(run.workload_hash!==paired[0].workload_hash||JSON.stringify(run.customer_samples)!==JSON.stringify(paired[0].customer_samples))throw Error("A/B 未使用同一批顾客");
        }
      }
      const means=new Map();
      if(result.baseline_cashiers!==groups[0] || result.summaries.length!==groups.length ||
          new Set(result.summaries.map(s=>s.cashiers)).size!==groups.length) throw Error("汇总分组错误");
      for(const summary of result.summaries) {
        if(!groups.includes(summary.cashiers))throw Error("未知汇总分组");
        const group=seeds.map(seed=>runs.find(r=>r.config.seed===seed&&r.config.cashiers===summary.cashiers));
        if(JSON.stringify(summary.result_ids)!==JSON.stringify(group.map(r=>r.result_id)))throw Error("汇总引用错误");
        for(const metric of ["average_wait_time","max_queue_length","simulation_end_time"]) {
          const values=group.map(r=>r.metrics[metric]), reported=summary[metric];
          if(!close(reported.mean,values.reduce((a,b)=>a+b,0)/values.length)||
              !close(reported.minimum,Math.min(...values))||!close(reported.maximum,Math.max(...values)))throw Error("汇总指标与独立实验不一致");
        }
        means.set(summary.cashiers,summary.average_wait_time.mean);
      }
      const seen=new Set();
      if(result.paired_differences.length!==seeds.length*(groups.length-1))throw Error("配对差值缺失");
      for(const d of result.paired_differences) {
        const key=`${d.seed}:${d.candidate_cashiers}`;
        if(seen.has(key)||!seeds.includes(d.seed)||!groups.slice(1).includes(d.candidate_cashiers))throw Error("配对差值分组错误");
        seen.add(key);
        const a=runs.find(r=>r.config.seed===d.seed&&r.config.cashiers===groups[0]),
          b=runs.find(r=>r.config.seed===d.seed&&r.config.cashiers===d.candidate_cashiers),
          delta=b.metrics.average_wait_time-a.metrics.average_wait_time;
        if(d.baseline_result_id!==a.result_id||d.candidate_result_id!==b.result_id||
            !close(d.average_wait_time_difference,delta)||
            d.max_queue_length_difference!==b.metrics.max_queue_length-a.metrics.max_queue_length||
            !close(d.simulation_end_time_difference,b.metrics.simulation_end_time-a.metrics.simulation_end_time)||
            (a.metrics.average_wait_time===0?d.wait_time_reduction_percent!==null:
              d.wait_time_reduction_percent===null||!close(d.wait_time_reduction_percent,-delta/a.metrics.average_wait_time*100)))throw Error("配对差值与实验不一致");
      }
      const ranking=[...means.keys()].sort((a,b)=>means.get(a)-means.get(b)||a-b),
        winners=ranking.filter(c=>Math.abs(means.get(c)-means.get(ranking[0]))<=Math.max(1e-9,1e-9*Math.max(Math.abs(means.get(c)),Math.abs(means.get(ranking[0])))));
      if(JSON.stringify(result.ranking_cashiers)!==JSON.stringify(ranking)||
          JSON.stringify(result.winner_cashiers)!==JSON.stringify(winners))throw Error("实验排名与均值不一致");
    }
    return runs;
  }
  window.Sim2ClipReplay = {createReplay, validateEvidence};
})();
