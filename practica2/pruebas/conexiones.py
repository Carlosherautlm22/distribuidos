import time,uuid,psycopg
# Run inside API container: python -m pruebas.conexiones is not available;
# use docker compose exec -T api python < pruebas/conexiones.py (Bash).

tag='idle_test_'+uuid.uuid4().hex[:8]

def appconn(label):
    return psycopg.connect(host='pg_master',dbname='labdb',user='app_user',password='apppass',application_name=tag+label,autocommit=True)
a=appconn('_idle');b=appconn('_txn')
a.execute('SELECT 1');b.execute('BEGIN');b.execute('SELECT 1')

with psycopg.connect(host='pg_master',dbname='labdb',user='labuser',password='labpass',autocommit=True) as admin:
    before=admin.execute("SELECT count(*) FROM pg_stat_activity WHERE application_name LIKE %s",(tag+'%',)).fetchone()[0]
    assert before==2,before
    time.sleep(45)
    after=admin.execute("SELECT count(*) FROM pg_stat_activity WHERE application_name LIKE %s",(tag+'%',)).fetchone()[0]
    assert after==0,after
    assert admin.execute("SELECT count(*) FROM pg_stat_replication WHERE state='streaming'").fetchone()[0]==2
for c in (a,b): c.close()
with appconn('_fresh') as c: assert c.execute('SELECT 1').fetchone()[0]==1
print('Sesiones idle e idle in transaction cerradas; replicacion y nuevas conexiones OK')
