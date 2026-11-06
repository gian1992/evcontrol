# EV Control — Integrazione personalizzata per Home Assistant

*[English](README.md) · **Italiano***

Ricarica dell'auto elettrica a priorità solare per Home Assistant. Ogni pochi secondi
decide quanti ampere deve assorbire l'auto — dai pannelli, dalla batteria di casa, dalla
rete, o per niente — e la decisione la spiega in parole, invece di lasciartela dedurre
da un numero.

## Cosa fa

Quasi tutti i sistemi di ricarica solare rispondono a una sola domanda: *c'è surplus in
questo momento?* Basta, finché l'auto non deve essere pronta a un'ora precisa. Da lì in
poi, ricaricare solo col surplus manca la scadenza in silenzio.

EV Control pianifica all'indietro a partire da una scadenza. Tu dici entro quando l'auto
deve essere carica; lui calcola l'ultimo momento in cui ripiegare sulla rete basta ancora
ad arrivarci, e fino a quel momento prende solo ciò che il sole gli dà.

### Modalità di ricarica

| Modalità | Comportamento |
|---|---|
| `eco` | Solo surplus solare. Non preleva mai dalla rete per caricare. |
| `normal` | Una corrente di base garantita, più il surplus disponibile sopra di essa. |
| `power` | Tutto il disponibile: surplus, batteria di casa sopra la sua soglia minima, poi rete fino al limite contrattuale. |
| `auto` | Guidata dalla scadenza. Carica col surplus e ripiega sulla rete solo abbastanza tardi da finire comunque in tempo. |
| `auto-eco` | Come `auto`, ma più paziente: concede più tempo al sole prima di cedere alla rete. |

### Il pianificatore solare

`eco`, `auto` e `auto-eco` sono governate dal pianificatore in `smart_charging.py`, che è
più accorto di una semplice soglia sul surplus:

- **Anti-pendolamento.** Una volta avviata la ricarica, mantiene il minimo della
  colonnina finché i pannelli producono qualcosa, invece di fermarsi e ripartire a ogni
  nuvola. Alle auto non piace, e alcune si rifiutano di ripartire per minuti.
- **Riserva batteria adattiva.** La potenza tenuta da parte per la batteria di casa è
  ricavata dalle medie mobili di carico domestico e produzione, non da un valore fisso in
  watt.
- **Consapevole della previsione.** Con [Solcast](https://github.com/BJReplay/ha-solcast-solar)
  configurato, la previsione di produzione viene calibrata su ciò che l'impianto sta
  realmente facendo, così una nuvola di passaggio non ridimensiona il piano dell'intera
  giornata.
- **Un budget di arresti**, perché una giornata incerta non si trasformi in decine di
  cicli di avvio e arresto.

### Scadenze settimanali

Ogni giorno della settimana può avere una scadenza: il momento entro cui l'auto deve
essere pronta, e quanto deve essere carica a quel punto. I giorni senza orario
caricano semplicemente col surplus, senza niente da recuperare.

La percentuale vale 100% di default ed è ciò su cui il pianificatore calcola
all'indietro. Chiedere l'80% richiede molta meno energia — e salta la maggiorazione
CC/CV che si applica solo vicino alla carica completa — quindi l'ultimo momento utile
per passare alla rete si sposta di ore più tardi, e il sole ha più tempo per lavorare.
Quando l'auto raggiunge l'obiettivo la scadenza è soddisfatta: smette di contare come
urgente, e la ricarica da surplus prosegue oltre quella soglia gratis invece di
fermarsi lì.

La pianificazione è modificabile in tre modi, che scrivono tutti gli stessi valori:

- dalle opzioni dell'integrazione (*Impostazioni → Dispositivi e servizi → EV Control →
  Configura → Orari di ricarica*)
- con i servizi `evcontrol.set_schedule` e `evcontrol.clear_schedule`
- dalla card Oikos, che ha un popup apposito (vedi sotto)

## Installazione

### HACS

Aggiungi questo repository come repository personalizzato di tipo *Integrazione*,
installa **EV Control**, poi riavvia Home Assistant.

### Manuale

Copia `custom_components/evcontrol/` nella cartella `config/custom_components/` di Home
Assistant e riavvia.

Poi: *Impostazioni → Dispositivi e servizi → Aggiungi integrazione → EV Control*.

> Riavvio, non ricarica. Home Assistant tiene in cache i moduli Python già importati,
> quindi ricaricare la voce di configurazione non rilegge i file modificati.

## Configurazione

La procedura di installazione e la finestra delle opzioni sono costruite dalle stesse
sezioni, così non c'è niente che puoi impostare all'inizio e poi non ritrovi:

| Sezione | Cosa copre |
|---|---|
| Fotovoltaico | Sensori di produzione e di tensione di rete |
| Consumo casa | Carico domestico, e se include già l'auto |
| Batteria di casa | SOC, soglia minima e limite di scarica |
| Colonnina | Entità della corrente, pulsanti avvio/arresto, corrente min/max |
| Veicolo elettrico | SOC dell'auto, capacità della batteria, potenza di ricarica |
| Orari di ricarica | La scadenza settimanale, un orario per giorno |
| Modalità AUTO | Corrente di base e soglia SOC della fase 1 |
| Ricarica intelligente | Il pianificatore solare: soglie, mantenimenti, riserva batteria |
| Previsione solare (Solcast) | Entità di previsione, opzionali |
| Rete e aggiornamento | Limite contrattuale, tensione, frequenza di aggiornamento |

La finestra delle opzioni è un menu: entri in una sezione, confermi e torni al menu, con
le modifiche tenute in sospeso finché non scegli di salvarle.

## Entità

### Sensori

| Entità | Cosa ti dice |
|---|---|
| `sensor.ev_control_charging_status` | **Quello da mettere in dashboard.** Lo stato è cosa sta facendo; gli attributi portano il perché, il piano, e tutti i dati grezzi che leggono le card. |
| `sensor.ev_control_charging_outlook` | L'auto sarà pronta in tempo, e qual è l'ultimo momento utile per ripiegare sulla rete. |
| `sensor.ev_control_surplus_power` | Surplus solare (W) |
| `sensor.ev_control_available_charging_current` | La corrente richiesta alla colonnina (A) |

Più i sensori diagnostici per produzione, consumo di casa, potenza assorbita dall'auto,
entrambi i SOC, lo stato del pianificatore, il margine disponibile, la riserva batteria,
lo scambio con la rete, il conteggio degli arresti, il prossimo obiettivo, l'ora di
ripiego sulla rete e le durate di ricarica totale e rimanente.

### Comandi

- `select.ev_control_charging_mode` — Eco / Normal / Power / Auto / Auto-Eco
- `switch.ev_control_charging_enabled` — ricarica on/off
- `button.ev_control_start_charging`, `button.ev_control_stop_charging`
- Numeri: limite di potenza da rete, corrente di base e riserva batteria della modalità
  Normal, soglia SOC della fase 1 AUTO, riserva batteria del pianificatore, soglia di
  arresto solare, SOC minimo della batteria di casa

## Servizi

### `evcontrol.set_schedule`

Imposta la scadenza per uno o più giorni. Salva nella voce di configurazione e applica
**senza ricaricare l'integrazione**, così la sessione del pianificatore sopravvive alla
modifica.

```yaml
action: evcontrol.set_schedule
data:
  day: weekdays        # un giorno, una lista, "today", oppure "all" / "weekdays" / "weekend"
  time: "07:30"
  soc: 80              # facoltativo; se omesso il giorno tiene la percentuale che aveva
```

### `evcontrol.set_target_soc`

Cambia quanto deve essere carica l'auto, senza toccare gli orari. Accetta gli stessi
valori di `day` di `set_schedule`.

```yaml
action: evcontrol.set_target_soc
data:
  day: saturday
  soc: 80
```

### `evcontrol.clear_schedule`

```yaml
action: evcontrol.clear_schedule
data:
  day: saturday
```

La percentuale obiettivo del giorno resta: cancellare una scadenza di solito è una
cosa temporanea, ed è meno sorprendente ritrovarsela al suo posto che azzerata.

### `evcontrol.unblock_now`

Azzera le due attese che il pianificatore si impone da solo — la pausa minima dopo
uno stop e la conferma di avvio — così la partenza può avvenire al ciclo successivo.

Non fa partire la ricarica, non alza la corrente e non cambia modalità. Senza
surplus e senza scadenza vicina il pianificatore resta fermo, dicendo perché: quello
che viene tolto è l'attesa, non il giudizio. Il budget di stop giornaliero, il minimo
della batteria di casa e la protezione del tramonto sono limiti, non attese, e
restano al loro posto.

Disponibile anche come pulsante **Sblocca adesso** e come comando SBLOCCA della card,
che si accende solo mentre una delle due attese è davvero in corso (attributo
`smart_unblockable` sul sensore di stato).

### Gli altri

| Servizio | Campi |
|---|---|
| `evcontrol.set_mode` | `mode`: eco, normal, power, auto, auto-eco |
| `evcontrol.start_charging` | — |
| `evcontrol.stop_charging` | — |
| `evcontrol.set_speed` | `speed`: corrente di base in A per la modalità Normal |
| `evcontrol.unblock_now` | — |

`evcontrol.set_end_time` è un alias deprecato di `set_schedule`, mantenuto perché le
automazioni esistenti continuino a funzionare. `evcontrol.set_start_time` è inerte: non
esiste un orario di partenza configurabile, perché in AUTO il pianificatore lo ricava
dalla scadenza, dalla carica ancora necessaria e dalla previsione.

## Card per la dashboard

Due possibilità, entrambe leggono `sensor.ev_control_charging_status`:

- **Card Oikos** — quella mantenuta. Tab delle modalità, interruttori di ricarica e
  protezione smart, lo stato con il suo motivo, la riga del piano, le metriche in tempo
  reale, e un popup per creare, modificare ed eliminare le scadenze settimanali senza
  aprire le opzioni dell'integrazione.
- **Card Lovelace inclusa** — `custom_components/evcontrol/www/evcontrol-card.js`, che
  l'integrazione registra da sola su `/evcontrol/evcontrol-card.js`. Funziona con
  FullyKiosk e Android WebView 68+.

In `examples/` trovi i file YAML per la dashboard e una versione standalone superata
della card Lovelace, conservata come riferimento.

## Diagnostica

```yaml
logger:
  logs:
    custom_components.evcontrol: debug
```

L'attributo `dettaglio_tecnico` su `sensor.ev_control_charging_status` riporta il
ragionamento del pianificatore per il ciclo in corso.

## Licenza

MIT — vedi [LICENSE](LICENSE).
