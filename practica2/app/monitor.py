import json,time,uuid,threading,logging
from .common import sql,local,states,now,rabbit,topology
import pika

def probe(node):
    try:
        with sql(node,'monitor_user') as c: c.execute('SELECT 1')
        return True
    except Exception: return False

def converged(node):
    region=node.capitalize()
    try:
        with sql(node,'monitor_user') as c:
            rel=c.execute("SELECT srsubstate FROM pg_subscription_rel").fetchall()
            if not rel or any(r['srsubstate']!='r' for r in rel): return False
            a=c.execute('SELECT region,id,nombre,saldo,origen_ingreso,operacion_id FROM clientes ORDER BY region,id').fetchall()
        with sql('master','monitor_user') as c:
            b=c.execute('SELECT region,id,nombre,saldo,origen_ingreso,operacion_id FROM clientes WHERE region=%s ORDER BY region,id',(region,)).fetchall()
        return a==b
    except Exception: return False

def save(node,new,old):
    transition=new['state']!=old.get('state')
    new['sequence']=old.get('sequence',0)+(1 if transition else 0)
    with local() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute('INSERT OR REPLACE INTO nodes VALUES (?,?)',(node,json.dumps(new)))
        if transition:
            event=dict(event_id=str(uuid.uuid4()),incident_id=new['incident'],node=node,node_sequence=new['sequence'],previous_state=old.get('state','UNKNOWN'),current_state=new['state'],detected_at=now(),consecutive_failures=new['failures'],replication_verified=new.get('replication_verified',False),reason=new['reason'])
            c.execute('INSERT INTO outbox(event_id,body) VALUES (?,?)',(event['event_id'],json.dumps(event)))
            logging.info('Transicion %s',event)

def sender():
    while True:
        try:
            with rabbit() as conn:
                ch=conn.channel();topology(ch);ch.confirm_delivery()
                while True:
                    with local() as c: row=c.execute('SELECT * FROM outbox WHERE sent=0 ORDER BY n LIMIT 1').fetchone()
                    if row:
                        e=json.loads(row['body'])
                        ch.basic_publish(exchange='infra.eventos',routing_key='db.'+e['node']+'.'+e['current_state'].lower(),body=row['body'],mandatory=True,properties=pika.BasicProperties(delivery_mode=2,message_id=e['event_id']))
                        with local() as c: c.execute('UPDATE outbox SET sent=1 WHERE n=?',(row['n'],))
                    else:
                        conn.process_data_events(time_limit=.3)
        except Exception: logging.exception('RabbitMQ: reintento outbox');time.sleep(2)

def run():
    threading.Thread(target=sender,daemon=True).start()
    while True:
        before=states();alive={n:probe(n) for n in ('master','norte','sur')}
        for node in ('master','norte','sur'):
            old=before.get(node,{})
            new=dict(old)
            new.update(reachable=alive[node],checked_at=now(),checked_epoch=time.time())
            new.setdefault('incident',str(uuid.uuid4()));new.setdefault('successes',0)
            if not alive[node]:
                fails=old.get('failures',0)+1
                if old.get('failures',0)==0: new['incident']=str(uuid.uuid4())
                new.update(failures=fails,successes=0,eligible=False,state='DOWN' if fails>=3 else 'SUSPECT',reason='sql_unreachable_from_monitor',replication_verified=False)
            elif node=='master':
                new.update(failures=0,successes=old.get('successes',0)+1,state='UP',eligible=True,reason='sql_ok',replication_verified=False)
            else:
                eq=alive['master'] and converged(node)
                count=old.get('successes',0)+1 if eq else 0
                # During a MASTER outage, a previously READY, still reachable
                # replica may serve explicitly eventual regional reads.
                stale=not alive['master'] and old.get('state')=='READY'
                ready=count>=2 or stale
                new.update(failures=0,successes=count,eligible=ready,state='READY' if ready else 'RECOVERING',replication_verified=bool(eq and ready),reason='master_unavailable_stale_reads' if stale else ('data_verified' if ready else 'waiting_replication_and_validation'))
            save(node,new,old)
        time.sleep(3)
