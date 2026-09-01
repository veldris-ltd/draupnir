-- The application role is deliberately not a superuser.
--
-- SAD 11C constraint 3 puts row level security on the site scoped tables, and
-- PostgreSQL exempts superusers from every policy. Connecting as one would
-- make site isolation pass in development and fail in production, which is the
-- worst of the available outcomes.

CREATE ROLE draupnir
    LOGIN
    PASSWORD 'draupnir'
    NOSUPERUSER
    NOCREATEROLE
    NOBYPASSRLS;

ALTER DATABASE draupnir OWNER TO draupnir;
ALTER SCHEMA public OWNER TO draupnir;
GRANT ALL PRIVILEGES ON DATABASE draupnir TO draupnir;
