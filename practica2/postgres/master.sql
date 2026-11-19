GRANT INSERT ON public.clientes TO app_user;
CREATE TABLE public.operaciones (
 id uuid PRIMARY KEY,
 fingerprint text NOT NULL,
 resultado jsonb NOT NULL
);
GRANT SELECT,INSERT ON public.operaciones TO app_user;
CREATE ROLE replicador WITH LOGIN REPLICATION PASSWORD 'replpass';
GRANT CONNECT ON DATABASE labdb TO replicador;
GRANT USAGE ON SCHEMA public TO replicador;
GRANT SELECT ON public.clientes TO replicador;

-- Exclude TRUNCATE: row filters do not filter TRUNCATE.
CREATE PUBLICATION pub_clientes_norte FOR TABLE public.clientes
 WHERE(region='Norte') WITH(publish='insert,update,delete');
CREATE PUBLICATION pub_clientes_sur FOR TABLE public.clientes
 WHERE(region='Sur') WITH(publish='insert,update,delete');
