// Teste de carga (k6) contra as metas do planejamento:
//   consulta por código: p95 < 150 ms · busca: p95 < 400 ms · menos de 1% de erro.
// Rodar com scripts/teste_de_carga.sh (sobe uma cópia temporária da API sem os limites
// por minuto, que senão seriam o gargalo medido).

import http from "k6/http";
import { check } from "k6";

const URL = __ENV.URL;
const PARAMS = { headers: { Authorization: `Bearer ${__ENV.TOKEN}` } };

// Buscas reais de quem trabalha com TUSS: texto, sem acento, com erro de digitação e por código.
const BUSCAS = [
  ["22", "consulta consultorio"],
  ["22", "hemograma"],
  ["22", "consluta"],
  ["22", "tomografia"],
  ["22", "1010"],
  ["22", "4.03.01"],
  ["20", "dipirona"],
  ["20", "amoxicilina"],
  ["20", "paracetamol"],
  ["20", "insulina"],
];

export const options = {
  scenarios: {
    consulta: { executor: "constant-vus", vus: 5, duration: "30s", exec: "consulta" },
    busca: { executor: "constant-vus", vus: 2, duration: "30s", exec: "busca", startTime: "30s" },
  },
  thresholds: {
    "http_req_duration{scenario:consulta}": ["p(95)<150"],
    "http_req_duration{scenario:busca}": ["p(95)<400"],
    "http_req_failed{scenario:consulta}": ["rate<0.01"],
    "http_req_failed{scenario:busca}": ["rate<0.01"],
  },
  summaryTrendStats: ["med", "p(95)", "p(99)", "max"],
};

// Antes de medir: junta códigos reais das duas tabelas, percorrendo as páginas.
export function setup() {
  const codigos = [];
  for (const [tabela, paginas] of [["22", 30], ["20", 20]]) {
    let cursor = "";
    for (let i = 0; i < paginas; i++) {
      const r = http.get(`${URL}/v1/tabelas/${tabela}/conceitos?limite=200${cursor}`, PARAMS);
      const corpo = r.json();
      for (const item of corpo.itens) codigos.push([tabela, item.codigo]);
      if (!corpo.proximo_cursor) break;
      cursor = `&cursor=${corpo.proximo_cursor}`;
    }
  }
  if (codigos.length === 0) throw new Error("nenhum código carregado: importe as tabelas antes");
  return { codigos };
}

function sortear(lista) {
  return lista[Math.floor(Math.random() * lista.length)];
}

export function consulta(dados) {
  const [tabela, codigo] = sortear(dados.codigos);
  const r = http.get(`${URL}/v1/tabelas/${tabela}/conceitos/${codigo}`, PARAMS);
  check(r, { "200": (resposta) => resposta.status === 200 });
}

export function busca() {
  const [tabela, termo] = sortear(BUSCAS);
  const r = http.get(
    `${URL}/v1/tabelas/${tabela}/conceitos?q=${encodeURIComponent(termo)}`,
    PARAMS,
  );
  check(r, { "200": (resposta) => resposta.status === 200 });
}
