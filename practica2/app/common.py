import os,sqlite3,json,threading
from contextlib import contextmanager
from datetime import datetime,timezone
import psycopg,pika
from psycopg.rows import dict_row
HOSTS={'master':'pg_master','norte':'pg_nodo_norte','sur':'pg_nodo_sur'}
REGIONS={'norte':'Norte','sur':'Sur'}
LIMIT=threading.BoundedSemaphore(10)
@contextmanager
def sql(node,user='app_user',autocommit=False):
    pw={'app_user':'apppass','monitor_user':'monitorpass','labuser':'labpass'}[user]
    with LIMIT:
        with psycopg.connect(host=HOSTS[node],dbname='labdb',user=user,password=pw,
              connect_timeout=2,options='-c statement_timeout=2000',row_factory=dict_row,
              autocommit=autocommit) as c:
            yield c

def local():
    c=sqlite3.connect(os.environ.get('STATE_DB','/state/state.sqlite'),timeout=15)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA busy_timeout=15000')
    c.execute('PRAGMA synchronous=FULL')
    return c

def init_local():
    os.makedirs('/state',exist_ok=True)
    with local() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript('''
          CREATE TABLE IF NOT EXISTS nodes(node TEXT PRIMARY KEY,body TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS outbox(n INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT UNIQUE,body TEXT,sent INTEGER DEFAULT 0);
          CREATE TABLE IF NOT EXISTS events(event_id TEXT PRIMARY KEY,node TEXT,seq INTEGER,body TEXT,UNIQUE(node,seq));
        ''')

def states():
    with local() as c: return {r['node']:json.loads(r['body']) for r in c.execute('SELECT * FROM nodes')}

def now(): return datetime.now(timezone.utc).isoformat()

def rabbit():
    return pika.BlockingConnection(pika.URLParameters(os.environ.get('AMQP_URL','amqp://labuser:labpass@rabbitmq:5672/%2F?heartbeat=60')))

def topology(ch):
    ch.exchange_declare(exchange='infra.eventos',exchange_type='topic',durable=True)
    ch.exchange_declare(exchange='infra.dlx',exchange_type='direct',durable=True)
    ch.queue_declare(queue='infra.fallidos',durable=True)
    ch.queue_bind(queue='infra.fallidos',exchange='infra.dlx',routing_key='failed')
    ch.queue_declare(queue='infra.auditoria',durable=True,arguments={'x-dead-letter-exchange':'infra.dlx','x-dead-letter-routing-key':'failed'})
    ch.queue_bind(queue='infra.auditoria',exchange='infra.eventos',routing_key='db.#')
