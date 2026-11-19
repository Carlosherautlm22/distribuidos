import sys,json,time,uuid,subprocess,urllib.request,urllib.error,collections
BASE='http://localhost:8080'

def http(path,data=None):
    req=urllib.request.Request(BASE+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=30) as r: return r.status,json.load(r)
    except urllib.error.HTTPError as e: return e.code,json.load(e)

def docker(*args): return subprocess.check_output(['docker',*args],text=True).strip()

def sql(node,q):
    service={'master':'pg_master','norte':'pg_nodo_norte','sur':'pg_nodo_sur'}[node]
    return docker('compose','exec','-T',service,'psql','-U','labuser','-d','labdb','-At','-c',q)

def wait(fn,label,seconds=180):
    until=time.time()+seconds
    while time.time()<until:
        try:
            if fn(): return
        except Exception: pass
        time.sleep(2)
    raise AssertionError('Timeout: '+label)

def data(channel,i,region):
    return dict(id=i,region=region,nombre=f'Cliente {channel} {i}',saldo='150.00',operacion_id=str(uuid.uuid5(uuid.NAMESPACE_URL,f'practica2/{channel}/{i}')))

def insert(channel,i,region):
    d=data(channel,i,region);s,r=http('/api/clientes/'+channel,d);assert s in (200,201),(s,r)
    return d

def ready():
    s,n=http('/api/nodos')
    return s==200 and all(n.get(k,{}).get('state')=='READY' for k in ('norte','sur'))
def equivalent(node):
    cols='region,id,nombre,saldo,origen_ingreso,operacion_id'
    a=sql('master',f"SELECT {cols} FROM clientes WHERE region='{node.capitalize()}' ORDER BY region,id")
    b=sql(node,f'SELECT {cols} FROM clientes ORDER BY region,id')
    return a==b

def initial():
    assert sql('master','SELECT count(*) FROM clientes') in ('0','30'),'Use entorno limpio o complete su lote inicial'
    for i in range(1,11): insert('master',i,'Norte' if i<=5 else 'Sur')
    for node,start in [('norte',101),('sur',201)]:
        for i in range(start,start+10): insert(node,i,node.capitalize())
    wait(lambda:ready() and equivalent('norte') and equivalent('sur'),'Convergencia inicial')
    assert [sql(n,'SELECT count(*) FROM clientes') for n in ('master','norte','sur')]==['30','15','15']
    d=data('norte',101,'Norte');s,_=http('/api/clientes/norte',d);assert s==200
    s,_=http('/api/clientes/norte',dict(d,saldo='999.00'));assert s==409
    for node in ('norte','sur'):
        destinations=collections.Counter()
        for _ in range(20):
            status,r=http('/api/clientes?region='+node.capitalize());assert status==200
            assert all(x['region']==node.capitalize() for x in r['data'])
            destinations[r['served_by']]+=1
        assert destinations['master']==10 and destinations[node]==10,destinations
        print('Balanceo',node,dict(destinations))
    print('Inicial 30/15/15 e idempotencia: OK')

def incident(node):
    assert node in ('norte','sur')
    service='pg_nodo_'+node;cid=docker('compose','ps','-q',service);assert cid
    before=int(sql('master','SELECT count(*) FROM clientes'))
    assert before==(30 if node=='norte' else 40),'Ejecutar inicial, luego norte, luego sur'
    docker('network','disconnect','practica2_datos',cid)
    try:
        wait(lambda:http('/api/nodos')[1].get(node,{}).get('state')=='DOWN','deteccion DOWN')
        for _ in range(4):
            s,r=http('/api/clientes?region='+node.capitalize());assert s==200 and r['served_by']=='master'
        start=301 if node=='norte' else 401
        for i in range(start,start+10): insert(node,i,node.capitalize())
        assert int(sql('master','SELECT count(*) FROM clientes'))==before+10
    finally:
        docker('network','connect','--alias',service,'practica2_datos',cid)
    wait(lambda:ready() and equivalent(node),'Recuperacion y equivalencia')
    assert int(sql(node,'SELECT count(*) FROM clientes'))==25
    print('Incidente recuperado:',node,'conteos',[sql(n,'SELECT count(*) FROM clientes') for n in ('master','norte','sur')])

def broker():
    wait(ready,'READY antes de prueba broker')
    docker('compose','stop','rabbitmq')
    try:
        docker('compose','stop','pg_nodo_norte')
        wait(lambda:http('/api/nodos')[1].get('norte',{}).get('state')=='DOWN','DOWN sin broker')
        docker('compose','start','pg_nodo_norte')
        wait(ready,'Recuperacion sin broker')
    finally:
        docker('compose','start','pg_nodo_norte')
        docker('compose','start','rabbitmq')
    def drained():
        code="import sqlite3; c=sqlite3.connect('/state/state.sqlite'); print(c.execute('select count(*) from outbox where sent=0').fetchone()[0])"
        return docker('compose','exec','-T','monitor','python','-c',code)=='0'
    wait(drained,'Outbox reenviado')
    def audit_complete():
        code="import sqlite3; c=sqlite3.connect('/state/state.sqlite'); print(c.execute('select (select count(*) from outbox)-(select count(*) from events)').fetchone()[0])"
        return docker('compose','exec','-T','monitor','python','-c',code)=='0'
    wait(audit_complete,'Auditoria completa')
    s,events=http('/api/eventos');assert s==200
    for node in ('master','norte','sur'):
        seq=[e['node_sequence'] for e in events if e['node']==node]
        assert seq==list(range(1,len(seq)+1)),seq
    print('Broker recuperado, auditoria sin huecos ni doble efecto')

def master_failure():
    wait(ready,'READY inicial')
    docker('compose','stop','pg_master')
    try:
        wait(lambda:http('/api/nodos')[1].get('master',{}).get('state')=='DOWN','MASTER DOWN')
        s,r=http('/api/clientes/master',data('master',99999,'Norte'));assert s==503,(s,r)
        for region in ('Norte','Sur'):
            s,r=http('/api/clientes?region='+region);assert s==200 and r['served_by']==region.lower()
        s,_=http('/api/clientes?consistency=strong');assert s==503
        print('Sin promocion regional; escritura bloqueada y lecturas eventuales: OK')
    finally: docker('compose','start','pg_master')
    wait(ready,'Recuperacion MASTER')

if __name__=='__main__':
    mode=sys.argv[1] if len(sys.argv)>1 else 'inicial'
    if mode=='inicial': initial()
    elif mode in ('norte','sur'): incident(mode)
    elif mode=='rabbitmq': broker()
    elif mode=='master': master_failure()
    else: raise SystemExit('Modos: inicial | norte | sur | rabbitmq | master')
