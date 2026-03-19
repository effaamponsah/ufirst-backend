-- Create per-module schemas. Each schema gets its own DB role in production.
-- This script runs once when the Postgres container is first initialised.

CREATE SCHEMA IF NOT EXISTS identity;
CREATE SCHEMA IF NOT EXISTS wallet;
CREATE SCHEMA IF NOT EXISTS card;
CREATE SCHEMA IF NOT EXISTS transaction;
CREATE SCHEMA IF NOT EXISTS vendor;
CREATE SCHEMA IF NOT EXISTS compliance;
CREATE SCHEMA IF NOT EXISTS notification;
CREATE SCHEMA IF NOT EXISTS reporting;
