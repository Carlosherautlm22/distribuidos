from .common import sql

def run():
    for node in ('norte','sur'):
        name='sub_clientes_'+node
        with sql(node,'labuser',autocommit=True) as c:
            if not c.execute('SELECT 1 FROM pg_subscription WHERE subname=%s',(name,)).fetchone():
                # CREATE SUBSCRIPTION must run outside an explicit transaction.
                c.execute("SET statement_timeout=0")
                c.execute("CREATE SUBSCRIPTION "+name+" CONNECTION 'host=pg_master port=5432 dbname=labdb user=replicador password=replpass application_name="+name+"' PUBLICATION pub_clientes_"+node+" WITH(copy_data=true)")
                print('Creada',name,flush=True)
            else: print('Ya existe',name,flush=True)
if __name__=='__main__': run()
