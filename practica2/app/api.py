import json,uuid,hashlib,threading,time,sqlite3
from decimal import Decimal,InvalidOperation
import psycopg
from psycopg.types.json import Jsonb
from flask import Flask,request,jsonify
from .common import sql,states,local
app=Flask(__name__)
lock=threading.Lock();turn={'norte':0,'sur':0}

def normalized(x,channel):
    amount=Decimal(str(x['saldo']))
    if not amount.is_finite() or not 0<=amount<=Decimal('9999999999.99') or amount!=amount.quantize(Decimal('.01')): raise ValueError('Saldo invalido')
    if type(x['id']) is not int or not 0<x['id']<2**63: raise ValueError('ID invalido')
    if x['region'] not in ('Norte','Sur'): raise ValueError('Region invalida')
    if channel!='master' and x['region']!=channel.capitalize(): raise ValueError('Region no coincide con canal')
    if not isinstance(x['nombre'],str) or not 0<len(x['nombre'].strip())<=100: raise ValueError('Nombre invalido')
    return dict(id=x['id'],region=x['region'],nombre=x['nombre'].strip(),saldo=str(amount.quantize(Decimal('.01'))),origen_ingreso=channel.upper(),operacion_id=str(uuid.UUID(x['operacion_id'])))

@app.get('/health')
def health(): return {'status':'ok'}
@app.get('/api/nodos')
def nodes(): return states()
@app.get('/api/eventos')
def events():
    with local() as c: rows=c.execute('SELECT body FROM events ORDER BY node,seq').fetchall()
    return jsonify([json.loads(r['body']) for r in rows])
@app.post('/api/clientes/<channel>')
def create(channel):
    if channel not in ('master','norte','sur'): return {},404
    try: d=normalized(request.get_json(),channel)
    except (ValueError,KeyError,TypeError,InvalidOperation): return {'error':'JSON o datos invalidos'},400
    fp=hashlib.sha256(json.dumps(d,sort_keys=True).encode()).hexdigest()
    result=dict(d,written_to='pg_master')
    try:
        with sql('master') as c:
            inserted=c.execute('INSERT INTO operaciones VALUES (%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id',(d['operacion_id'],fp,Jsonb(result))).fetchone()
            old=c.execute('SELECT * FROM operaciones WHERE id=%s',(d['operacion_id'],)).fetchone()
            if old['fingerprint']!=fp: return {'error':'Operacion reutilizada con datos distintos'},409
            if inserted:
                c.execute('INSERT INTO clientes(region,id,nombre,saldo,origen_ingreso,operacion_id) VALUES (%s,%s,%s,%s,%s,%s)',(d['region'],d['id'],d['nombre'],Decimal(d['saldo']),d['origen_ingreso'],d['operacion_id']))
            result=old['resultado']
        return result,201 if inserted else 200
    except psycopg.IntegrityError: return {'error':'Clave de cliente u operacion existente'},409
    except psycopg.Error: return {'error':'MASTER no disponible; resultado potencialmente incierto: reintente con el mismo operacion_id'},503

def read(node,region):
    with sql(node) as c:
        return c.execute('SELECT * FROM clientes WHERE region=%s ORDER BY id',(region,)).fetchall() if region else c.execute('SELECT * FROM clientes ORDER BY region,id').fetchall()
@app.get('/api/clientes')
def get():
    region=request.args.get('region');cons=request.args.get('consistency','eventual')
    if region not in (None,'Norte','Sur') or cons not in ('eventual','strong'): return {'error':'Filtro invalido'},400
    target='master';node=region.lower() if region else None
    st=states();master_ok=st.get('master',{}).get('reachable',False)
    eligible=node and st.get(node,{}).get('eligible',False) and time.time()-st.get(node,{}).get('checked_epoch',0)<30
    if cons=='eventual' and eligible:
        with lock:
            turn[node]+=1
            if not master_ok or turn[node]%2: target=node
    try: rows=read(target,region)
    except psycopg.Error:
        if target=='master': return {'error':'MASTER no disponible para esta lectura'},503
        target='master'
        try: rows=read(target,region)
        except psycopg.Error: return {'error':'Ni replica seleccionada ni MASTER disponibles'},503
    return jsonify(data=rows,served_by=target,region=region,consistency='strong' if target=='master' else 'eventual',warning='Replica potencialmente desactualizada' if target!='master' else None)
