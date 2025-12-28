SET client_encoding = 'UTF8';

CREATE TABLE IF NOT EXISTS public._utf8_probe (
  id  serial PRIMARY KEY,
  txt text NOT NULL
);

TRUNCATE public._utf8_probe;

INSERT INTO public._utf8_probe(txt) VALUES
  ('Қазақша тест'),
  ('русский текст'),
  ('emoji ✅🔥');

SELECT id, txt FROM public._utf8_probe ORDER BY id;