CREATE ROLE app_user LOGIN PASSWORD 'apppass';
CREATE ROLE monitor_user LOGIN PASSWORD 'monitorpass';
GRANT pg_monitor TO monitor_user;

GRANT CONNECT ON DATABASE labdb TO app_user, monitor_user;
GRANT USAGE ON SCHEMA public TO app_user, monitor_user;

ALTER ROLE app_user SET idle_session_timeout = '40s';
ALTER ROLE app_user SET idle_in_transaction_session_timeout = '40s';
ALTER ROLE app_user SET statement_timeout = '10s';

CREATE TABLE public.clientes (
 region text NOT NULL CHECK(region IN ('Norte','Sur')),
 id bigint NOT NULL,
 nombre varchar(100) NOT NULL,
 saldo numeric(12,2) NOT NULL CHECK(saldo>=0),
 origen_ingreso text NOT NULL CHECK(origen_ingreso IN ('MASTER','NORTE','SUR')),
 operacion_id uuid NOT NULL UNIQUE,
 creado_en timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(region,id)
);
GRANT SELECT ON public.clientes TO app_user,monitor_user;
