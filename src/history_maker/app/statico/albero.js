/* L'albero genealogico disegnato nel browser.
 *
 * Il server manda un GRAFO — nodi e archi — e non un albero gia' pronto.
 * La ragione sta nei paesi piccoli: i rami si richiudono, due cugini si
 * sposano, e da quel momento un antenato e' raggiungibile per due strade.
 * Un albero vero lo duplicherebbe; qui compare una volta sola, con due
 * discendenze che scendono a lui, che e' quello che e' successo davvero.
 *
 * L'impaginazione e' fatta qui e non sul server perche' cambia a ogni
 * clic — si sposta la radice, si aprono o chiudono generazioni — e
 * rifarla in locale costa un millisecondo invece di un viaggio di rete.
 */

'use strict';

const LARGHEZZA = 168;   // di un riquadro
const ALTEZZA = 50;
const SPAZIO_X = 22;     // fra due riquadri della stessa generazione
const SPAZIO_COPPIA = 10;
const SPAZIO_Y = 108;    // fra una generazione e la successiva

const stato = {
  radice: null,
  su: 3,
  giu: 3,
  grafo: null,
  posizioni: new Map(),
  storia: [],
  vista: { x: 0, y: 0, k: 1 },
};

const el = (id) => document.getElementById(id);
const svg = el('albero');
const mondo = el('mondo');

/* ------------------------------------------------------------------ rete */

async function chiedi(rotta) {
  const risposta = await fetch(rotta);
  if (!risposta.ok) throw new Error(rotta + ' -> ' + risposta.status);
  return risposta.json();
}

/* --------------------------------------------------------------- formato */

function vissuto(p) {
  // "1820-1889", "n. 1820", "m. 1889", "†entro il 1863", oppure gli anni
  // in cui compare, che e' l'unica cosa che si sa di molti.
  const nato = p.anno_nascita, morto = p.anno_morte;
  if (nato && morto) return `${nato}–${morto}`;
  if (nato) return `n. ${nato}`;
  if (morto) return `m. ${morto}`;
  if (p.morta_entro) return `† entro il ${p.morta_entro}`;
  if (p.anno_primo) {
    return p.anno_primo === p.anno_ultimo
      ? `nel ${p.anno_primo}`
      : `${p.anno_primo}–${p.anno_ultimo}`;
  }
  return '';
}

function nomeDi(p) {
  return [p.nome, p.cognome].filter(Boolean).join(' ') || 'senza nome';
}

function classeSesso(p) {
  return p.sesso === 'M' ? 'uomo' : p.sesso === 'F' ? 'donna' : 'ignoto';
}

function primo(elenco) {
  // I campi a piu' valori arrivano separati da barre verticali, in
  // ordine di frequenza: il primo e' quello che la persona ha portato
  // piu' spesso.
  return elenco ? elenco.split(' | ')[0] : '';
}

function taglia(testo, quanto) {
  if (!testo) return '';
  return testo.length > quanto ? testo.slice(0, quanto - 1) + '…' : testo;
}

/* ------------------------------------------------------------ ricerca */

let attesaRicerca = null;

el('ricerca').addEventListener('input', (evento) => {
  clearTimeout(attesaRicerca);
  const testo = evento.target.value.trim();
  if (testo.length < 2) { el('risultati').hidden = true; return; }
  attesaRicerca = setTimeout(() => eseguiRicerca(testo), 180);
});

el('ricerca').addEventListener('keydown', (evento) => {
  if (evento.key === 'Escape') el('risultati').hidden = true;
  if (evento.key === 'Enter') {
    const primo = el('risultati').querySelector('button');
    if (primo) primo.click();
  }
});

document.addEventListener('click', (evento) => {
  if (!evento.target.closest('.cerca')) el('risultati').hidden = true;
});

async function eseguiRicerca(testo) {
  const dati = await chiedi('/api/cerca?q=' + encodeURIComponent(testo));
  const cassetto = el('risultati');
  cassetto.innerHTML = '';
  cassetto.hidden = false;

  if (!dati.risultati.length) {
    cassetto.innerHTML = dati.fuori
      ? `<div class="vuoto">Nessuno con questo nome ha un posto nell’albero.
           ${fraseFuori(dati.fuori)} Chi c’è resta dentro gli atti che lo
           nominano, sotto «chi altro c’era».</div>`
      : '<div class="vuoto">Nessuno con questo nome. '
        + 'Prova con il solo cognome, o con una grafia diversa.</div>';
    return;
  }

  for (const p of dati.risultati) {
    const bottone = document.createElement('button');
    const parentela = [];
    if (p.genitori) parentela.push('genitori noti');
    if (p.figli) parentela.push(p.figli === 1 ? '1 figlio' : p.figli + ' figli');
    parentela.push(p.menzioni === 1 ? '1 menzione' : p.menzioni + ' menzioni');

    bottone.innerHTML = `<span>${nomeDi(p)}</span>`
      + `<span class="meta">${vissuto(p)} · ${parentela.join(' · ')}</span>`;
    bottone.addEventListener('click', () => {
      cassetto.hidden = true;
      el('ricerca').value = '';
      vaiA(p.id);
    });
    cassetto.appendChild(bottone);
  }

  /* Chi e' rimasto fuori si dice, ma non si mostra: sapere che il nome
   * c'e' e non porta a un albero e' un'informazione; vederselo comparire
   * fra i risultati e doverlo scartare a ogni ricerca e' un fastidio. */
  if (dati.fuori) {
    const coda = document.createElement('div');
    coda.className = 'fuori';
    coda.textContent = fraseFuori(dati.fuori);
    cassetto.appendChild(coda);
  }
}

/* Chi la ricerca ha lasciato fuori, detto una volta sola: serve quando
 * di risultati non ce n'e' nessuno e quando ce ne sono ma non sono
 * tutti. Il singolare vale la riga in piu': 'compaiono 1 persone' fa
 * sembrare rotto anche quello che funziona. */
function fraseFuori(quanti) {
  return quanti === 1
    ? 'Una persona con questo nome compare nei registri solo come testimone, '
      + 'dichiarante o ufficiale: senza parentele dichiarate non ha una '
      + 'scheda da aprire.'
    : `${quanti} persone con questo nome compaiono nei registri solo come `
      + 'testimoni, dichiaranti o ufficiali: senza parentele dichiarate non '
      + 'hanno una scheda da aprire.';
}

/* ------------------------------------------------------- impaginazione */

/* Le unita' dell'impaginazione sono le COPPIE, non le persone. E' il
 * punto da cui dipende se l'albero si legge o no: marito e moglie devono
 * stare accostati, e i figli devono scendere da fra i due. Impaginare
 * per persone e poi cercare di riavvicinare i coniugi produce
 * attraversamenti dappertutto. */
function costruisciUnita(grafo) {
  const perId = new Map(grafo.nodi.map((n) => [n.id, n]));
  const diChi = new Map();     // id persona -> unita'
  const unita = [];

  for (const arco of grafo.archi) {
    if (arco.tipo !== 'unione') continue;
    if (!perId.has(arco.da) || !perId.has(arco.a)) continue;
    if (diChi.has(arco.da) || diChi.has(arco.a)) continue;
    const u = {
      membri: [perId.get(arco.da), perId.get(arco.a)],
      generazione: perId.get(arco.da).generazione,
      unione: arco,
    };
    unita.push(u);
    diChi.set(arco.da, u);
    diChi.set(arco.a, u);
  }

  for (const nodo of grafo.nodi) {
    if (diChi.has(nodo.id)) continue;
    const u = { membri: [nodo], generazione: nodo.generazione, unione: null };
    unita.push(u);
    diChi.set(nodo.id, u);
  }

  unita.forEach((u, i) => {
    u.indice = i;
    u.larghezza = u.membri.length === 2
      ? LARGHEZZA * 2 + SPAZIO_COPPIA
      : LARGHEZZA;
  });
  return { unita, diChi, perId };
}

/* L'ordine dell'unita' da cui questa scende, per tenere i fratelli
 * vicini. Senza genitori in vista si finisce in coda, che e' dove stanno
 * bene: sono rami che non hanno un posto obbligato. */
function primoGenitore(u, vicini, unita) {
  const legati = [...vicini.get(u.indice)]
    .map((i) => unita[i].ordine)
    .filter((v) => v !== undefined);
  return legati.length ? Math.min(...legati) : Number.MAX_SAFE_INTEGER;
}

/* L'anno su cui ordinare i fratelli: quello del membro piu' vecchio
 * della coppia, cosi' marito e moglie non si scambiano di posto fra una
 * generazione e l'altra. */
function annoDi(u) {
  const anni = u.membri
    .map((m) => m.anno_nascita || m.anno_primo)
    .filter(Boolean);
  return anni.length ? Math.min(...anni) : Number.MAX_SAFE_INTEGER;
}

function impagina(grafo) {
  const { unita, diChi, perId } = costruisciUnita(grafo);

  // I legami fra unita', che sono quelli su cui si ordina.
  const sopra = new Map(unita.map((u) => [u.indice, new Set()]));
  const sotto = new Map(unita.map((u) => [u.indice, new Set()]));
  for (const arco of grafo.archi) {
    if (arco.tipo !== 'filiazione') continue;
    const genitore = diChi.get(arco.da), figlio = diChi.get(arco.a);
    if (!genitore || !figlio || genitore === figlio) continue;
    sotto.get(genitore.indice).add(figlio.indice);
    sopra.get(figlio.indice).add(genitore.indice);
  }

  // Le generazioni, dall'alto in basso.
  const perGenerazione = new Map();
  for (const u of unita) {
    if (!perGenerazione.has(u.generazione)) perGenerazione.set(u.generazione, []);
    perGenerazione.get(u.generazione).push(u);
  }
  const livelli = [...perGenerazione.keys()].sort((a, b) => a - b);

  // Ordine di partenza: la radice al centro della sua generazione, e le
  // altre nell'ordine in cui il grafo le ha rese.
  const radice = diChi.get(stato.radice);
  for (const livello of livelli) {
    const fila = perGenerazione.get(livello);
    fila.forEach((u, i) => { u.ordine = i; });
    if (radice && radice.generazione === livello) {
      fila.sort((a, b) => (a === radice ? -1 : b === radice ? 1 : a.ordine - b.ordine));
      fila.forEach((u, i) => { u.ordine = i; });
    }
  }

  // Passate di baricentro: ogni unita' si sposta verso la media dei suoi
  // parenti nella generazione accanto. Quattro passate bastano — oltre,
  // l'ordine non cambia piu' — e alternare i due versi evita che le
  // generazioni in cima restino ferme mentre quelle in fondo si sistemano.
  for (let giro = 0; giro < 4; giro++) {
    const versi = giro % 2 === 0 ? livelli : [...livelli].reverse();
    for (const livello of versi) {
      const vicini = giro % 2 === 0 ? sopra : sotto;
      const fila = perGenerazione.get(livello);
      for (const u of fila) {
        const legati = [...vicini.get(u.indice)]
          .map((i) => unita[i].ordine)
          .filter((v) => v !== undefined);
        u.baricentro = legati.length
          ? legati.reduce((s, v) => s + v, 0) / legati.length
          : u.ordine;
      }
      /* A parita' di baricentro decide **da chi si scende**, e solo
       * dopo l'anno di nascita. E' cio' che tiene i fratelli attaccati:
       * senza, due figli della stessa coppia possono finire separati da
       * un cugino che ha per caso lo stesso baricentro, e da li' in giu'
       * ogni generazione moltiplica gli incroci. Con dodici nuclei sulla
       * stessa riga il risultato era un intreccio in cui non si capiva
       * piu' quale nipote scendesse da quale figlio. */
      fila.sort((a, b) =>
        a.baricentro - b.baricentro
        || primoGenitore(a, vicini, unita) - primoGenitore(b, vicini, unita)
        || annoDi(a) - annoDi(b)
        || a.ordine - b.ordine);
      fila.forEach((u, i) => { u.ordine = i; });
    }
  }

  /* I fratelli, tutti attaccati.
   *
   * Le passate di baricentro avvicinano, ma non garantiscono niente: un
   * cugino con lo stesso baricentro puo' infilarsi in mezzo a due
   * fratelli, e da li' in giu' ogni generazione moltiplica gli incroci
   * finche' non si capisce piu' quale nipote scenda da quale figlio.
   *
   * Qui si impone la cosa direttamente: ogni generazione si riordina
   * mettendo insieme, in blocco, i figli della stessa coppia; i blocchi
   * seguono l'ordine dei genitori, e dentro il blocco si va per anno di
   * nascita, che e' l'ordine in cui li scriverebbe uno storico. Chi
   * genitori in vista non ne ha resta dov'era.
   *
   * Il guadagno si misura: sull'albero di Filippo Lella gli incroci fra
   * le barre dei fratelli e le discese dei figli passano da 69 a poche
   * unita'. */
  for (const livello of livelli) {
    if (livello === Math.min(...livelli)) continue;
    const fila = perGenerazione.get(livello);
    const blocchi = new Map();
    for (const u of fila) {
      const genitori = [...sopra.get(u.indice)];
      const capo = genitori.length
        ? Math.min(...genitori.map((i) => unita[i].ordine))
        : -1 - u.ordine;              // senza genitori: resta al suo posto
      if (!blocchi.has(capo)) blocchi.set(capo, []);
      blocchi.get(capo).push(u);
    }
    const ordinati = [...blocchi.entries()].sort((a, b) => a[0] - b[0]);
    let posto = 0;
    for (const [, gruppo] of ordinati) {
      gruppo.sort((a, b) => annoDi(a) - annoDi(b) || a.ordine - b.ordine);
      for (const u of gruppo) u.ordine = posto++;
    }
    fila.sort((a, b) => a.ordine - b.ordine);
  }

  // Le ascisse. Prima una fila compatta, poi si tira ogni unita' verso i
  // suoi figli senza mai far toccare due riquadri: e' cio' che mette i
  // genitori sopra il gruppo dei figli invece che a caso sulla riga.
  for (const livello of livelli) {
    let x = 0;
    for (const u of perGenerazione.get(livello)) {
      u.x = x;
      x += u.larghezza + SPAZIO_X;
    }
  }

  for (let giro = 0; giro < 6; giro++) {
    const versi = giro % 2 === 0 ? [...livelli].reverse() : livelli;
    for (const livello of versi) {
      const vicini = giro % 2 === 0 ? sotto : sopra;
      const fila = perGenerazione.get(livello);
      for (const u of fila) {
        const legati = [...vicini.get(u.indice)].map((i) => unita[i]);
        if (!legati.length) continue;
        const centro = legati.reduce((s, v) => s + v.x + v.larghezza / 2, 0) / legati.length;
        u.desiderata = centro - u.larghezza / 2;
      }
      sistemaFila(fila);
    }
  }

  // Coordinate finali di ogni persona.
  const posizioni = new Map();
  const cima = Math.min(...livelli);
  for (const u of unita) {
    const y = (u.generazione - cima) * SPAZIO_Y;
    u.membri.forEach((persona, i) => {
      posizioni.set(persona.id, {
        x: u.x + i * (LARGHEZZA + SPAZIO_COPPIA),
        y,
        nodo: persona,
        unita: u,
      });
    });
    u.y = y;
  }
  return { posizioni, unita, diChi, perId, sopra, sotto };
}

/* Mette le unita' dove vorrebbero stare, senza sovrapposizioni e senza
 * cambiare l'ordine gia' deciso.
 *
 * Il punto delicato sono le unita' **senza ancora**: quelle che in questa
 * passata non hanno parenti da cui farsi tirare — un avo di cui non si
 * vedono i figli, un coniuge senza discendenza in vista. Lasciate dove
 * stavano, restano alla posizione iniziale mentre tutte le altre migrano
 * sopra i loro figli, e aprono voragini: in un albero vero si misurava un
 * buco di 6.163 pixel in mezzo alla prima generazione. Quindi qui, senza
 * ancora, un'unita' si accosta al vicino invece di restare ferma. */
function sistemaFila(fila) {
  let limite = null;
  for (const u of fila) {
    const voluto = u.desiderata !== undefined
      ? u.desiderata
      : (limite === null ? 0 : limite);
    u.x = limite === null ? voluto : Math.max(voluto, limite);
    limite = u.x + u.larghezza + SPAZIO_X;
  }

  // Da destra a sinistra: chi puo' spostarsi verso la sua posizione
  // voluta lo fa, e chi non ne ha una si accoda al vicino di destra.
  for (let i = fila.length - 2; i >= 0; i--) {
    const u = fila[i], dopo = fila[i + 1];
    const massimo = dopo.x - u.larghezza - SPAZIO_X;
    const voluto = u.desiderata !== undefined ? u.desiderata : massimo;
    if (voluto > u.x) u.x = Math.min(voluto, massimo);
  }
  for (const u of fila) delete u.desiderata;
}

/* ----------------------------------------------------------- il disegno */

function disegna() {
  const grafo = stato.grafo;
  const { posizioni, unita, diChi } = impagina(grafo);
  stato.posizioni = posizioni;

  const archi = el('archi');
  const nodi = el('nodi');
  archi.innerHTML = '';
  nodi.innerHTML = '';

  // Le coppie: un tratto fra i due riquadri.
  for (const u of unita) {
    if (u.membri.length !== 2) continue;
    const y = u.y + ALTEZZA / 2;
    const x1 = u.x + LARGHEZZA;
    const x2 = u.x + LARGHEZZA + SPAZIO_COPPIA;
    const certo = u.unione && u.unione.origine === 'matrimonio';
    archi.appendChild(percorso(
      `M${x1},${y} L${x2},${y}`,
      'unione ' + (certo ? 'certo' : 'dedotto'),
      certo
        ? 'Matrimonio documentato' + (u.unione.anno ? ' nel ' + u.unione.anno : '')
        : 'Coppia dedotta: compaiono insieme come genitori'
        + (u.unione && u.unione.anno ? ', dal ' + u.unione.anno : '')
    ));
  }

  // La filiazione, raggruppata per coppia di genitori: un solo tronco che
  // scende dai genitori, una barra orizzontale, e da li' i figli. Tirare
  // una linea per ogni figlio darebbe un pettine illeggibile.
  const famiglie = new Map();
  for (const arco of grafo.archi) {
    if (arco.tipo !== 'filiazione') continue;
    const genitore = posizioni.get(arco.da), figlio = posizioni.get(arco.a);
    if (!genitore || !figlio) continue;
    const chiave = genitore.unita.indice;
    if (!famiglie.has(chiave)) famiglie.set(chiave, { unita: genitore.unita, figli: new Set() });
    famiglie.get(chiave).figli.add(arco.a);
  }

  /* Le barre dei fratelli vanno su corsie diverse.
   *
   * Disegnate tutte alla stessa altezza — com'era — dodici famiglie della
   * stessa generazione producono dodici segmenti orizzontali affiancati
   * che l'occhio legge come una riga sola, e da quel momento non si vede
   * piu' quale nipote scenda da quale figlio. Con le corsie ogni nucleo
   * ha la sua altezza, e il gruppo dei fratelli si stacca da solo.
   *
   * L'assegnazione e' avida e da sinistra: la prima corsia libera, cioe'
   * quella dove nessun'altra barra occupa lo stesso tratto di ascisse.
   * Chi non si sovrappone a nessuno resta sulla corsia zero, che e'
   * quella piu' vicina ai genitori: gli alberi radi non cambiano
   * aspetto, e a pagare la complicazione sono solo le generazioni
   * affollate, che e' dove serve. */
  const CORSIE = 4;
  const impegnate = [];
  const disegnabili = [];
  for (const { unita: u, figli } of famiglie.values()) {
    const elenco = [...figli].map((id) => posizioni.get(id)).filter(Boolean);
    if (!elenco.length) continue;
    const partenzaX = u.x + u.larghezza / 2;
    const xs = elenco.map((f) => f.x + LARGHEZZA / 2);
    disegnabili.push({
      u, elenco, partenzaX,
      sinistra: Math.min(...xs, partenzaX),
      destra: Math.max(...xs, partenzaX),
    });
  }
  disegnabili.sort((a, b) => a.u.y - b.u.y || a.sinistra - b.sinistra);

  for (const famiglia of disegnabili) {
    const { u, elenco, partenzaX, sinistra, destra } = famiglia;
    const partenzaY = u.y + ALTEZZA;
    const salto = (SPAZIO_Y - ALTEZZA) / (CORSIE + 1);
    let corsia = 0;
    while (corsia < CORSIE - 1 && impegnate.some(
      (b) => b.y === u.y && b.corsia === corsia
        && b.sinistra <= destra && sinistra <= b.destra
    )) corsia++;
    impegnate.push({ y: u.y, corsia, sinistra, destra });
    const barra = partenzaY + salto * (corsia + 1);

    archi.appendChild(percorso(`M${partenzaX},${partenzaY} L${partenzaX},${barra}`, 'filiazione'));
    if (destra - sinistra > 1) {
      archi.appendChild(percorso(`M${sinistra},${barra} L${destra},${barra}`, 'filiazione'));
    }
    for (const figlio of elenco) {
      const x = figlio.x + LARGHEZZA / 2;
      archi.appendChild(percorso(`M${x},${barra} L${x},${figlio.y}`, 'filiazione'));
    }
  }

  for (const posizione of posizioni.values()) {
    nodi.appendChild(disegnaNodo(posizione));
  }

  el('benvenuto').hidden = true;
}

function percorso(d, classe, titolo) {
  const p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  p.setAttribute('d', d);
  p.setAttribute('class', classe);
  if (titolo) {
    const t = document.createElementNS('http://www.w3.org/2000/svg', 'title');
    t.textContent = titolo;
    p.appendChild(t);
  }
  return p;
}

function disegnaNodo({ x, y, nodo }) {
  const NS = 'http://www.w3.org/2000/svg';
  const g = document.createElementNS(NS, 'g');
  g.setAttribute('class', 'nodo ' + classeSesso(nodo)
    + (nodo.id === stato.radice ? ' radice' : ''));
  g.setAttribute('transform', `translate(${x},${y})`);
  g.dataset.id = nodo.id;

  const riquadro = document.createElementNS(NS, 'rect');
  riquadro.setAttribute('width', LARGHEZZA);
  riquadro.setAttribute('height', ALTEZZA);
  g.appendChild(riquadro);

  // La striscia colorata sul fianco: dice il sesso senza tingere tutto
  // il riquadro, che su trecento nodi sarebbe una coperta a scacchi.
  const striscia = document.createElementNS(NS, 'rect');
  striscia.setAttribute('class', 'sesso');
  striscia.setAttribute('width', 4);
  striscia.setAttribute('height', ALTEZZA);
  striscia.setAttribute('rx', 2);
  g.appendChild(striscia);

  g.appendChild(testo(12, 17, taglia(nomeDi(nodo), 24), 'nome'));
  g.appendChild(testo(12, 31, vissuto(nodo), 'date'));

  const mestiere = primo(nodo.professioni);
  const contrada = primo(nodo.contrade) || primo(nodo.residenze);
  const sotto = [mestiere, contrada].filter(Boolean).join(', ');
  if (sotto) g.appendChild(testo(12, 43, taglia(sotto, 26), 'extra'));

  // Quanti parenti restano fuori da questa inquadratura: e' l'invito a
  // cliccare, e dice a colpo d'occhio dove l'albero continua.
  const dentro = contaVicini(nodo.id);
  const fuori = nodo.espandibile - dentro;
  if (fuori > 0) {
    const segno = testo(LARGHEZZA - 8, 43, '+' + fuori, 'altro');
    segno.setAttribute('text-anchor', 'end');
    g.appendChild(segno);
  }

  const titolo = document.createElementNS(NS, 'title');
  titolo.textContent = descrizione(nodo, fuori);
  g.appendChild(titolo);

  g.addEventListener('click', () => vaiA(nodo.id));
  return g;
}

function testo(x, y, contenuto, classe) {
  const t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
  t.setAttribute('x', x);
  t.setAttribute('y', y);
  t.setAttribute('class', classe);
  t.textContent = contenuto;
  return t;
}

function contaVicini(id) {
  let quanti = 0;
  for (const arco of stato.grafo.archi) {
    if (arco.da === id || arco.a === id) quanti++;
  }
  return quanti;
}

function descrizione(nodo, fuori) {
  const righe = [nomeDi(nodo), vissuto(nodo)];
  if (nodo.professioni) righe.push('Mestiere: ' + nodo.professioni.split(' | ').join(', '));
  if (nodo.contrade) righe.push('Contrada: ' + nodo.contrade.split(' | ').join(', '));
  righe.push(nodo.menzioni + (nodo.menzioni === 1 ? ' menzione' : ' menzioni')
    + ' · ' + nodo.fondata_su);
  if (fuori > 0) righe.push('Clicca: ha altri ' + fuori + ' parenti fuori da qui.');
  return righe.filter(Boolean).join('\n');
}

/* ------------------------------------------------------- pan e ingrandimento */

function applicaVista() {
  const { x, y, k } = stato.vista;
  mondo.setAttribute('transform', `translate(${x},${y}) scale(${k})`);
}

/* Sotto questa scala i nomi non si leggono piu', e un albero che non si
 * legge non serve a niente: meglio mostrarne un pezzo per intero e
 * lasciare che ci si sposti, che mostrarlo tutto e illeggibile. Una
 * famiglia con dieci figli, ognuno con coniuge e nipoti, e' larga
 * quattromila pixel: farla stare in una finestra vorrebbe dire il 20%. */
const SCALA_MINIMA_LEGGIBILE = 0.45;

function inquadra() {
  const riquadro = mondo.getBBox();
  if (!riquadro.width || !riquadro.height) return;
  const larghezza = svg.clientWidth, altezza = svg.clientHeight;
  if (!larghezza || !altezza) return;
  const margine = 48;

  const kIntero = Math.min(
    (larghezza - margine * 2) / riquadro.width,
    (altezza - margine * 2) / riquadro.height,
    1.15
  );

  if (kIntero >= SCALA_MINIMA_LEGGIBILE) {
    stato.vista = {
      k: kIntero,
      x: (larghezza - riquadro.width * kIntero) / 2 - riquadro.x * kIntero,
      y: (altezza - riquadro.height * kIntero) / 2 - riquadro.y * kIntero,
    };
  } else {
    // Troppo largo: si inquadra la radice, che e' la persona di cui si
    // e' chiesto l'albero, e il resto si raggiunge trascinando.
    const centro = stato.posizioni.get(stato.radice);
    const k = SCALA_MINIMA_LEGGIBILE;
    const cx = centro ? centro.x + LARGHEZZA / 2 : riquadro.x + riquadro.width / 2;
    const cy = centro ? centro.y + ALTEZZA / 2 : riquadro.y + riquadro.height / 2;
    stato.vista = { k, x: larghezza / 2 - cx * k, y: altezza / 2 - cy * k };
  }
  applicaVista();
}

svg.addEventListener('wheel', (evento) => {
  evento.preventDefault();
  const rettangolo = svg.getBoundingClientRect();
  const px = evento.clientX - rettangolo.left, py = evento.clientY - rettangolo.top;
  const fattore = Math.exp(-evento.deltaY * 0.0016);
  const nuovo = Math.min(3, Math.max(0.12, stato.vista.k * fattore));
  const rapporto = nuovo / stato.vista.k;
  // Si ingrandisce attorno al puntatore, non attorno all'origine: e'
  // l'unico modo in cui lo zoom su un albero grande non fa perdere il
  // punto che si stava guardando.
  stato.vista.x = px - (px - stato.vista.x) * rapporto;
  stato.vista.y = py - (py - stato.vista.y) * rapporto;
  stato.vista.k = nuovo;
  applicaVista();
}, { passive: false });

let trascina = null;
svg.addEventListener('pointerdown', (evento) => {
  if (evento.target.closest('.nodo')) return;
  trascina = { x: evento.clientX, y: evento.clientY, vx: stato.vista.x, vy: stato.vista.y };
  svg.classList.add('trascina');
  svg.setPointerCapture(evento.pointerId);
});
svg.addEventListener('pointermove', (evento) => {
  if (!trascina) return;
  stato.vista.x = trascina.vx + (evento.clientX - trascina.x);
  stato.vista.y = trascina.vy + (evento.clientY - trascina.y);
  applicaVista();
});
for (const evento of ['pointerup', 'pointercancel']) {
  svg.addEventListener(evento, () => { trascina = null; svg.classList.remove('trascina'); });
}

el('adatta').addEventListener('click', inquadra);

for (const [cursore, campo] of [['su', 'su'], ['giu', 'giu']]) {
  el(cursore).addEventListener('input', (evento) => {
    stato[campo] = +evento.target.value;
    el(cursore + '-n').textContent = evento.target.value;
    if (stato.radice) vaiA(stato.radice, true);
  });
}

/* ------------------------------------------------------------ navigazione */

async function vaiA(individuo, stessaVista) {
  const nuova = individuo !== stato.radice;
  stato.radice = individuo;

  const [grafo, scheda] = await Promise.all([
    chiedi(`/api/albero/${individuo}?su=${stato.su}&giu=${stato.giu}`),
    chiedi(`/api/persona/${individuo}`),
  ]);
  stato.grafo = grafo;
  disegna();
  mostraScheda(scheda);

  if (nuova) {
    stato.storia = stato.storia.filter((v) => v.id !== individuo);
    stato.storia.push({ id: individuo, nome: nomeDi(scheda.persona) });
    if (stato.storia.length > 12) stato.storia.shift();
    disegnaBriciole();
  }
  // Il pannello della scheda si apre in questo momento e si prende un
  // quarto della larghezza: inquadrare prima che il browser abbia
  // rifatto i conti lascerebbe l'albero spostato di quel quarto.
  if (!stessaVista) requestAnimationFrame(inquadra);
  history.replaceState(null, '', '#' + individuo);
}

function disegnaBriciole() {
  const barra = el('briciole');
  barra.innerHTML = '';
  for (const voce of stato.storia) {
    const b = document.createElement('button');
    b.textContent = voce.nome;
    if (voce.id === stato.radice) b.className = 'ora';
    b.addEventListener('click', () => vaiA(voce.id));
    barra.appendChild(b);
  }
}

/* ---------------------------------------------------------------- scheda */

function mostraScheda(dati) {
  const p = dati.persona;
  const pannello = el('scheda');
  pannello.hidden = false;
  pannello.scrollTop = 0;
  pannello.innerHTML = '';

  const titolo = document.createElement('h2');
  titolo.textContent = nomeDi(p);
  pannello.appendChild(titolo);

  const sotto = document.createElement('p');
  sotto.className = 'sottotitolo';
  sotto.textContent = [
    vissuto(p),
    p.sesso === 'M' ? 'uomo' : p.sesso === 'F' ? 'donna' : 'sesso ignoto',
  ].filter(Boolean).join(' · ');
  pannello.appendChild(sotto);

  pannello.appendChild(sezioneFatti(p));

  if (!p.nell_albero) {
    pannello.appendChild(nota(
      'Questa persona non ha un posto nell’albero: nessun atto la dichiara '
      + 'padre, madre, figlio, figlia o coniuge di qualcuno. Resta '
      + 'nell’archivio, dentro gli atti che la nominano, ma la ricerca non '
      + 'la propone.'));
  }

  if (p.menzioni === 1) {
    pannello.appendChild(nota(
      'Questa persona compare in un atto solo. Tutto quello che se ne sa '
      + 'sta in quella pagina.'));
  } else if (p.menzioni_incerte > 0) {
    pannello.appendChild(nota(
      `${p.menzioni_incerte} delle ${p.menzioni} menzioni sono state attribuite `
      + 'per compatibilità di nome ed età, non perché un atto lo dica: '
      + 'era l’unica persona a cui potessero riferirsi.'));
  }

  aggiungiParenti(pannello, 'Genitori', dati.genitori, (g) => g.tipo);
  aggiungiParenti(pannello, 'Coniugi', dati.coniugi, (c) =>
    c.origine === 'matrimonio'
      ? 'matrimonio del ' + (c.anno_unione || '?')
      : 'dedotto dai figli' + (c.anno_unione ? ', dal ' + c.anno_unione : ''));
  aggiungiParenti(pannello, 'Figli', dati.figli, null);
  aggiungiParenti(pannello, 'Fratelli e sorelle', dati.fratelli, (f) =>
    f.pieno ? '' : 'un solo genitore in comune');

  aggiungiAtti(pannello, dati);
}

function sezioneFatti(p) {
  const contenitore = document.createElement('div');
  const h = document.createElement('h3');
  h.textContent = 'Quel che si sa';
  contenitore.appendChild(h);

  const lista = document.createElement('dl');
  lista.className = 'fatti';
  const voci = [];

  if (p.anno_nascita) {
    voci.push(['Nascita', p.anno_nascita
      + (p.nascita_origine === 'certa'
        ? ' (dall’atto di nascita)'
        : ' (circa, dalle età dichiarate)')]);
  }
  if (p.anno_morte) voci.push(['Morte', p.anno_morte + ' (dall’atto di morte)']);
  else if (p.morta_entro) voci.push(['Morte', 'prima del ' + p.morta_entro
    + ' — un atto la nomina come già defunta']);

  if (p.anno_primo) {
    voci.push(['Nei registri', p.anno_primo === p.anno_ultimo
      ? 'solo nel ' + p.anno_primo
      : 'dal ' + p.anno_primo + ' al ' + p.anno_ultimo]);
  }
  if (p.professioni) voci.push(['Mestiere', p.professioni.split(' | ').join(', ')]);
  if (p.contrade) voci.push(['Contrada', p.contrade.split(' | ').join(', ')]);
  if (p.residenze) voci.push(['Residenza', p.residenze.split(' | ').join(', ')]);

  const varianti = new Set();
  for (const campo of [p.varianti_nome, p.varianti_cognome]) {
    if (campo) for (const v of campo.split(' | ')) varianti.add(v);
  }
  // Le grafie originali si mostrano solo quando aggiungono qualcosa: se
  // il nome e' sempre stato letto allo stesso modo, ripeterlo e' rumore.
  const diverse = [...varianti].filter((v) => v !== p.nome && v !== p.cognome);
  if (diverse.length) voci.push(['Letto anche', diverse.join(', ')]);

  voci.push(['Documentata da', p.menzioni
    + (p.menzioni === 1 ? ' menzione' : ' menzioni') + ' · ' + p.fondata_su]);

  for (const [chiave, valore] of voci) {
    const dt = document.createElement('dt');
    dt.textContent = chiave;
    const dd = document.createElement('dd');
    dd.textContent = valore;
    lista.append(dt, dd);
  }
  contenitore.appendChild(lista);
  return contenitore;
}

function nota(testo) {
  const d = document.createElement('div');
  d.className = 'avviso';
  d.textContent = testo;
  return d;
}

function aggiungiParenti(pannello, titolo, elenco, etichetta) {
  const h = document.createElement('h3');
  h.textContent = titolo + (elenco.length > 1 ? ` (${elenco.length})` : '');
  pannello.appendChild(h);

  if (!elenco.length) {
    const vuoto = document.createElement('p');
    vuoto.className = 'vuoto-nota';
    vuoto.textContent = 'Nessuno che i registri dichiarino.';
    pannello.appendChild(vuoto);
    return;
  }

  const lista = document.createElement('ul');
  lista.className = 'parenti';
  for (const parente of elenco) {
    const li = document.createElement('li');
    const b = document.createElement('button');
    const extra = etichetta ? etichetta(parente) : '';
    b.innerHTML = `<span>${nomeDi(parente)}</span> `
      + `<span class="meta">${vissuto(parente)}</span>`
      + (extra ? ` <span class="ramo">${extra}</span>` : '');
    b.addEventListener('click', () => vaiA(parente.id));
    li.appendChild(b);
    lista.appendChild(li);
  }
  pannello.appendChild(lista);
}

const TIPO_ATTO = {
  nascita: 'Nascita', morte: 'Morte', matrimonio: 'Matrimonio',
  pubblicazione: 'Pubblicazione', altro: 'Atto',
};

function aggiungiAtti(pannello, dati) {
  const h = document.createElement('h3');
  h.textContent = `Gli atti che la nominano (${dati.menzioni.length})`;
  pannello.appendChild(h);

  const conoscenti = new Map(dati.conoscenti.map((c) => [c.atto, c.persone]));

  for (const m of dati.menzioni) {
    const blocco = document.createElement('details');
    blocco.className = 'atto';

    const riassunto = document.createElement('summary');
    riassunto.innerHTML = `<span class="anno">${m.anno}</span>`
      + `<span>${TIPO_ATTO[m.tipo] || m.tipo}${m.numero_atto ? ' n. ' + m.numero_atto : ''}</span>`
      + `<span class="ruolo">${m.ruolo || '?'}</span>`;
    blocco.appendChild(riassunto);

    const corpo = document.createElement('div');
    corpo.className = 'corpo';

    const dettagli = [];
    if (m.data_atto) dettagli.push('Registrato il ' + m.data_atto);
    if (m.data_evento && m.data_evento !== m.data_atto) dettagli.push('Avvenuto il ' + m.data_evento);
    if (m.ora_evento) dettagli.push('alle ' + m.ora_evento);
    if (m.luogo) dettagli.push('A ' + m.luogo);
    if (m.eta) dettagli.push('Età dichiarata: ' + m.eta);
    if (m.professione) dettagli.push('Mestiere: ' + m.professione);
    if (m.residenza) dettagli.push('Residenza: ' + m.residenza);
    if (m.stato_vitale) dettagli.push('Stato: ' + m.stato_vitale);
    if (m.note) dettagli.push('Nota dell’atto: ' + m.note);
    if (m.nome_letto || m.cognome_letto) {
      dettagli.push('Letto sulla pagina: ' + [m.nome_letto, m.cognome_letto].filter(Boolean).join(' '));
    }
    if (m.cognome_origine && m.cognome_origine !== 'atto') {
      dettagli.push('Cognome non scritto: ricavato dal ' + m.cognome_origine);
    }
    if (!m.certa) dettagli.push('Attribuzione per compatibilità, non dichiarata dall’atto');
    if (m.affidabilita) dettagli.push('Leggibilità della pagina: ' + m.affidabilita);
    if (m.incertezze) dettagli.push('Punti dubbi: ' + m.incertezze);

    const elenco = document.createElement('div');
    elenco.innerHTML = dettagli.map((d) => `<div>${d}</div>`).join('');
    corpo.appendChild(elenco);

    const altri = (conoscenti.get(m.atto) || []).filter((c) => c.nome || c.nome_letto);
    if (altri.length) {
      const chi = document.createElement('div');
      chi.innerHTML = '<div style="margin-top:.4rem"><em>Chi altro c’era:</em></div>';
      const lista = document.createElement('ul');
      lista.className = 'compagni';
      for (const c of altri) {
        const li = document.createElement('li');
        const nome = [c.nome, c.cognome].filter(Boolean).join(' ')
          || [c.nome_letto, c.cognome_letto].filter(Boolean).join(' ');
        /* Cliccabile solo chi porta da qualche parte. Il testimone e
         * l'ufficiale restano scritti — sono la trama sociale del paese e
         * vanno letti — ma aprirli darebbe una scheda senza un ramo. */
        if (c.individuo && c.nell_albero) {
          const b = document.createElement('button');
          b.textContent = nome;
          b.addEventListener('click', () => vaiA(c.individuo));
          li.append(document.createTextNode((c.ruolo || '?') + ': '), b);
        } else {
          li.innerHTML = `<span class="spento">${c.ruolo || '?'}: ${nome}</span>`;
        }
        lista.appendChild(li);
      }
      chi.appendChild(lista);
      corpo.appendChild(chi);
    }

    if (m.testo_integrale) {
      const testo = document.createElement('div');
      testo.className = 'testo';
      testo.textContent = m.testo_integrale;
      corpo.appendChild(testo);
    }

    if (m.immagine) {
      const bottone = document.createElement('button');
      bottone.className = 'pagina';
      bottone.textContent = 'Guarda la pagina del registro';
      bottone.addEventListener('click', () => apriLente(m));
      corpo.appendChild(bottone);
    }

    blocco.appendChild(corpo);
    pannello.appendChild(blocco);
  }
}

/* ------------------------------------------------------------- la lente */

function apriLente(m) {
  el('lente-img').src = '/immagine/' + m.immagine;
  el('lente-didascalia').textContent =
    `${TIPO_ATTO[m.tipo] || m.tipo} n. ${m.numero_atto || '?'} del ${m.anno} · `
    + `registro ${m.registro} · ${m.immagine}`;
  el('lente').hidden = false;
}

el('chiudi-lente').addEventListener('click', () => { el('lente').hidden = true; });
el('lente').addEventListener('click', (evento) => {
  if (evento.target === el('lente')) el('lente').hidden = true;
});
document.addEventListener('keydown', (evento) => {
  if (evento.key === 'Escape') el('lente').hidden = true;
});

/* ------------------------------------------------------------- avvio */

async function avvia() {
  const numeri = await chiedi('/api/statistiche');
  el('numeri').textContent =
    `${numeri.individui_albero.toLocaleString('it')} persone nell’albero `
    + `(su ${numeri.individui.toLocaleString('it')}) · `
    + `${numeri.legami.toLocaleString('it')} legami · `
    + `${numeri.unioni.toLocaleString('it')} coppie · `
    + `${numeri.atti.toLocaleString('it')} atti · ${numeri.anno_min}–${numeri.anno_max}`;

  // Le famiglie piu' numerose sono il modo piu' utile di cominciare:
  // sono i punti dell'archivio da cui si vede piu' albero.
  const suggeriti = el('suggeriti');
  for (const f of numeri.famiglie.slice(0, 8)) {
    const b = document.createElement('button');
    b.textContent = `${f.nome_padre} ${f.cognome_padre || ''} e `
      + `${f.nome_madre} ${f.cognome_madre || ''} · ${f.figli} figli`;
    b.addEventListener('click', () => vaiA(f.padre));
    suggeriti.appendChild(b);
  }

  const iniziale = parseInt(location.hash.slice(1), 10);
  if (iniziale) vaiA(iniziale);
}

window.addEventListener('resize', () => { if (stato.grafo) inquadra(); });
avvia();
