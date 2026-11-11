# Contribuire a MyDeck

Correzioni, nuove letture hardware, miglioramenti di accessibilità e documentazione sono benvenuti.

## Sviluppo locale

Esegui il fork della repository e apri la cartella del progetto su Linux. Sono necessari Python 3.10+ con `curses` e Bash per gli script di installazione. Non ci sono pacchetti da installare con `pip`.

```bash
python3 -m mydeck
python3 -m unittest discover -s tests -v
```

Per provare le icone avanzate usa `python3 -m mydeck --icons nerd` con un Nerd Font selezionato nel terminale.

## Dove intervenire

| File | Responsabilità |
| --- | --- |
| `mydeck/system.py` | Lettura della telemetria Linux |
| `mydeck/app.py` | Interfaccia curses, navigazione e campionamento |
| `mydeck/overview.py` | Layout dei pannelli e icone |
| `mydeck/graphs.py` | Grafici Braille e interpolazione |
| `tests/test_mydeck.py` | Test della raccolta dati e dell’interfaccia |

Mantieni la raccolta in sola lettura e il progetto privo di dipendenze Python esterne. Gestisci esplicitamente dati mancanti, permessi insufficienti e hardware senza sensori. La visualizzazione deve restare utilizzabile da tastiera e con le icone Unicode standard.

## Proporre una modifica

Apri un branch dedicato e limita la pull request a un cambiamento coerente. Descrivi il problema, il comportamento ottenuto e le verifiche eseguite. Per modifiche visive aggiungi uno screenshot o una breve registrazione del terminale, indicando dimensioni e font.

Per correzioni della logica, aggiungi un test che riproduca il problema quando utile. Per cambiamenti all’interfaccia verifica navigazione, ricerca, pausa e ridimensionamento: almeno 70 × 16, 120 × 34 e 140 × 40. La pipeline GitHub esegue i test su Linux con Python 3.10–3.14.

## Segnalare un problema

Includi distribuzione e versione di Python, terminale, valore di `TERM`, dimensioni della finestra e passaggi per riprodurre il problema. Se riguarda i sensori o systemd, specifica anche il contesto hardware o l’eventuale uso di un container. Prima di allegare screenshot o campioni JSON, rimuovi i dati del tuo computer che non vuoi rendere pubblici.

I contributi sono distribuiti con la [licenza MIT](LICENSE) del progetto.
