import json,time,sqlite3,logging
from .common import rabbit,topology,local

def run():
    while True:
        try:
            with rabbit() as conn:
                ch=conn.channel();topology(ch);ch.basic_qos(prefetch_count=1)
                def callback(ch,method,props,body):
                    try:
                        e=json.loads(body)
                        for k in ('event_id','node','node_sequence','current_state'): assert k in e
                    except Exception:
                        ch.basic_nack(method.delivery_tag,requeue=False);return
                    try:
                        with local() as c:
                            duplicate=c.execute('SELECT 1 FROM events WHERE event_id=?',(e['event_id'],)).fetchone()
                            if not duplicate:
                                last=c.execute('SELECT max(seq) n FROM events WHERE node=?',(e['node'],)).fetchone()['n'] or 0
                                if e['node_sequence']!=last+1: raise ValueError('Secuencia no contigua')
                                c.execute('INSERT INTO events VALUES (?,?,?,?)',(e['event_id'],e['node'],e['node_sequence'],json.dumps(e)))
                    except sqlite3.OperationalError:
                        time.sleep(1);ch.basic_nack(method.delivery_tag,requeue=True);return
                    except Exception:
                        logging.exception('Evento invalido: DLQ');ch.basic_nack(method.delivery_tag,requeue=False);return
                    ch.basic_ack(method.delivery_tag)
                ch.basic_consume(queue='infra.auditoria',on_message_callback=callback,auto_ack=False)
                ch.start_consuming()
        except Exception: logging.exception('Consumidor reconectando');time.sleep(2)
