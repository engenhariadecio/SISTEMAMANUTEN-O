# Sistema Centralizado de Manutenção — Décio Metalúrgica

Sistema web completo de gestão de manutenção industrial e predial, substituindo os
formulários do Microsoft Forms e as planilhas de controle usadas hoje
(DC-014, Solicitação de OS, Monitoramento de Materiais, check lists RQ).

Integra o **Sistema NLAG** (controle do depósito) como módulo nativo.

---

## Índice

1. [O que o sistema faz](#o-que-o-sistema-faz)
2. [De onde veio cada funcionalidade](#de-onde-veio-cada-funcionalidade)
3. [Perfis de acesso](#perfis-de-acesso)
4. [Publicar no GitHub](#1-publicar-no-github)
5. [Publicar no Railway](#2-publicar-no-railway)
6. [Primeiro acesso e carga inicial](#3-primeiro-acesso-e-carga-inicial)
7. [Rodar na sua máquina](#rodar-na-sua-máquina)
8. [Estrutura do projeto](#estrutura-do-projeto)
9. [Backup e manutenção](#backup-e-manutenção)

---

## O que o sistema faz

### Fluxo de uma corretiva

```
  SOLICITANTE            LÍDER                 MANUTENTOR            ANALISTA
       │                   │                       │                    │
   abre a OS ──────► fica na TRIAGEM               │                    │
       │             (e-mail p/ liderança)         │                    │
       │                   │                       │                    │
       │            escolhe o manutentor ──► é notificado por e-mail    │
       │                   │                       │                    │
       │                   │                 inicia · cronômetro        │
       │                   │                       │                    │
       │                   │                 pede peça ──► tem saldo? ──┤
       │                   │                       │      sim → baixa   │
       │                   │                       │      não → formulário
       │                   │                       │                    │
       │                   │                 conclui (defeito + causa)  │
       │                   │                       │                    │
  ◄─── e-mail: resolvido? ─────────────────────────┘                    │
       │                                                                │
   aprova → FINALIZADA                                                  │
   reprova → volta para o MESMO manutentor
```

A OS **não vai direto para a equipe**: ela para na tela de triagem do líder, que
escolhe quem atende. Enquanto isso nenhum manutentor é notificado nem consegue
iniciar. A tela de triagem mostra a carga de cada manutentor (quantas OS em aberto,
quem está executando agora) para ajudar a equilibrar a distribuição.

Exceção: a **intervenção de emergência** não passa por triagem — o manutentor abre
para si mesmo e o cronômetro começa na hora.

Uma OS reprovada **volta para o mesmo manutentor**, não para a fila de triagem.

### Módulo — Manutenções Corretivas
- Abertura de OS pelo solicitante: estabelecimento → tipo → centro de trabalho →
  equipamento → problema → anexos (foto/vídeo/PDF).
- Opção **“Outro”** quando o equipamento ainda não está cadastrado.
- **Criticidade configurável (A–E)** herdada automaticamente do equipamento; a fila
  é ordenada por ela e por “máquina parada”. Cada nível tem nome, cor, ordem de
  prioridade e prazos de resposta/conclusão editáveis em *Admin → Criticidade*.
- **Matriz de classificação**: seis critérios ponderados (segurança e meio ambiente 30%,
  impacto na produção 25%, qualidade 15%, frequência de falha 10%, dificuldade de
  reparo 10%, redundância 10%) geram uma pontuação de 0 a 100 que sugere o nível.
  A escala se redistribui sozinha se você criar ou desativar níveis. Permite override
  manual justificado, com registro em auditoria.
- Máquina parada sobe **um nível** de prioridade na abertura da OS.
- Cronômetro no chão de fábrica: **Iniciar · Pausar · Almoço · Aguardando peça · Concluir**.
  Cada intervalo é gravado separadamente, permitindo apurar tempo real de reparo.
- Apontamentos automáticos: quem assumiu, material solicitado, pausas, conclusão.
  O solicitante é notificado a cada evento.
- Solicitação de peça (cadastro / expansão / transferência) direto de dentro da OS.
- Conclusão exige **tipo de defeito** e **causa** (listas cadastráveis).
- Aprovação do solicitante: **Aprovado** finaliza a OS; **Reprovado** exige comentário
  e devolve a OS para a fila com status “Reprovada”.
- Pedido de peça com baixa automática no NLAG ou solicitação ao analista (ver abaixo).
- **Intervenção automática**: o manutentor abre para si mesmo (máquina + sintoma),
  o cronômetro inicia e o tempo fica registrado no histórico do equipamento.
- **Modo tablet**: fila em cartões grandes, otimizada para uso na máquina.

### Módulo — Manutenções Preventivas
- Cadastro centralizado de check lists por equipamento (substitui os arquivos RQ).
- Grade visual de **52 semanas** com previsto × realizado, igual à DC-014.
- Geração de OMs por semana ou item a item, a partir da programação.
- Salvamento automático do check list — sem pastas de rede.
- Apontamento de tempo pelo manutentor, usado pelo analista para dar baixa na OM.
- Preenchimento na máquina via tablet, com **fotos e observações por item**.
- Upload de **relatórios de terceiros** direto na OM.
- **Item marcado como NOK gera automaticamente uma OS de corretiva planejada.**
- Campo de **horas da máquina (horímetro)** que atualiza o cadastro do equipamento.
- **Visto do líder** que recebe a liberação da máquina.
- Reprogramações registradas com justificativa e histórico.
- Tolerância configurável (padrão 7 dias): preventiva fora do prazo não soma
  no indicador do mês seguinte.

### Sub-módulo — Rondas Diárias de Inspeção
- Check list diário de pontos de vários equipamentos (abastecimento de água,
  lubrificação da monovia, compressor, secador, purgadores, ETE, gerador…).
- Registro com foto por ponto (câmera do celular/tablet).
- Ponto fora do padrão → **OS de corretiva planejada gerada automaticamente**,
  já com a foto anexada.

### Módulo — Controle de Materiais
- **NLAG**: saldo controlado pelo sistema — entradas, saídas, ajuste de inventário,
  histórico completo, etiquetas com código de barras Code128 e coletor.
- **HIBE/ERSA**: saldo importado da planilha do SAP, apenas para consulta de
  disponibilidade (sem controle de saldo).
- Quando o manutentor aplica material na OS, o saldo NLAG é baixado na hora.
- Estoque mínimo/máximo por item, marcação de **peça crítica**.
- **Alertas de estoque mínimo** com sugestão de quantidade de compra e consumo
  dos últimos 30 dias; relatório semanal enviado ao comprador (cópia para
  analista, líder e supervisão).
- **Plano de materiais**: “roda” a grade de 52 semanas dentro de um horizonte
  em **dias** (7/15/30/60/90/180, valor livre até 730, ou um período exato) e
  cruza a necessidade com o saldo atual, classificando cada item em
  **Em estoque · Parcial · Sem estoque · Não cadastrado**.
  Mostra falta, custo estimado, data da primeira preventiva e quais equipamentos
  consomem o item. Opção de reservar o estoque mínimo, exportação em CSV e
  geração das solicitações de compra direto da lista.
- Importadores prontos para as planilhas atuais (aba SALDO NLAG, aba SALDO
  HIBE-ERSA do SAP e cadastro geral) — reconhece as colunas automaticamente.

### Solicitação de Material
Substitui o formulário + a planilha “Monitoramento de Solicitação de Materiais”,
com o fluxo completo de situações:

```
Solicitado → Em cadastro → Cadastrado → Proc. de Compra → Pedido SAP
→ Aguardando Cotação → Pendente Aprovação → Compra Aprovada
→ Recebido → Concluído          (ou Recusado / Cancelado)
```

Campos de acompanhamento: Nº Ficha/FDS, ID 4MDG, Nº PR, código final SAP,
datas de cadastro e chegada, centro de custo, OS vinculada.
Quando o material chega, o manutentor da OS é notificado para retomar o serviço.

### Módulo — Indicadores
- Visão geral do parque fabril com os equipamentos **parados em vermelho**.
- OS abertas × em andamento × aguardando material × aguardando aprovação.
- **MTBF** — tempo médio entre falhas, por equipamento.
- **MTTR** — tempo médio de reparo, global e por equipamento.
- **% de atendimento de preventivas** — previstas × realizadas, mês a mês.
- Atendimento de preventivas por responsável (com % no prazo).
- Atendimento de OS por responsável (com tempo médio).
- **Custos × equipamento** — peças + mão de obra (custo-hora configurável).
- Disponibilidade por equipamento.
- Defeitos e causas mais frequentes.
- Ficha completa do equipamento: histórico de corretivas, preventivas e terceiros.

### Notificações por e-mail

Sete eventos disparam e-mail automaticamente, cada um ligável em
*Administração → E-mail* sem precisar de novo deploy:

| Evento | Quem recebe |
|---|---|
| OS aberta | **líder e supervisão** (para a triagem) |
| OS atribuída | **o manutentor escolhido pelo líder** |
| **OS concluída** | **quem abriu a OS**, com botão de aprovar/reprovar |
| OS aprovada | o manutentor que executou |
| OS reprovada | manutentor, líder e supervisão |
| Peça solicitada | analista de materiais e líder |
| Material recebido | o manutentor da OS |
| Preventiva atribuída | o manutentor escolhido pelo líder |
| Ronda destinada | o manutentor escolhido pelo líder |

Os e-mails saem em HTML com a identidade visual da empresa, uma tabela de
detalhes (equipamento, criticidade, defeito, tempo de reparo) e um botão que
leva direto à tela certa. Também vai uma versão em texto puro, para clientes
que não renderizam HTML.

**Configuração pela tela**, em *Administração → E-mail* — nenhuma variável de
ambiente é necessária:

1. Escolha o provedor (Gmail, Outlook/Microsoft 365, Brevo ou outro). Servidor,
   porta e segurança são preenchidos sozinhos.
2. Informe o e-mail que vai enviar e a senha.
3. Salve e use o botão **Testar**.

A senha é gravada **criptografada** com uma chave derivada da `SECRET_KEY`,
nunca aparece na tela e é excluída dos backups em Excel e JSON.
Se a `SECRET_KEY` for trocada, a senha antiga fica ilegível e a tela avisa para
cadastrá-la de novo — nada mais quebra.

O **endereço do sistema** usado nos links é detectado automaticamente a partir
do próprio acesso. Só preencha se quiser forçar um domínio específico.

**Provedores disponíveis**, cada um com o passo a passo dentro da própria tela:

| Provedor | Exige 2FA? | Observação |
|---|---|---|
| **Gmail** *(padrão)* | Sim | 500 e-mails/dia · use uma conta exclusiva do sistema |
| **Brevo** | Não | 300 e-mails/dia grátis · usa uma chave SMTP do painel |
| **SendGrid** | Não | 100/dia grátis · o usuário é sempre `apikey` |
| **Outlook / Microsoft 365** | Depende | Muitos tenants bloqueiam autenticação básica |
| **Outro servidor** | Não | Relay interno da empresa; muitos dispensam login |

> **Gmail:** crie uma conta Google **exclusiva do sistema**, que ninguém use no
> dia a dia. A verificação em duas etapas fica no celular do administrador e não
> atrapalha ninguém. Gere a senha de aplicativo em
> `myaccount.google.com/apppasswords` — o Google escondeu esse link da tela de
> Segurança, então use o endereço direto.
>
> Se por algum motivo não for possível ativar o 2FA, use o **Brevo**: ele
> autentica com uma chave própria e envia *em nome* do endereço que você quiser,
> depois de confirmá-lo no painel.

As variáveis `SMTP_*` continuam funcionando como valor inicial, mas o que
estiver salvo pela tela tem prioridade.

**Cada e-mail leva à tela exata da ação:**

| Evento | Para onde o botão leva |
|---|---|
| Nova OS | tela de triagem |
| OS atribuída | a OS, no bloco do cronômetro |
| OS concluída | a OS, no bloco de aprovação |
| OS reprovada | a OS, no cronômetro |
| Peça solicitada | a solicitação, pronta para tratar |
| Material recebido | a OS, para retomar |

O bloco de destino pisca ao abrir, para não se perder na tela.

A tela de administração mostra o estado da configuração, a última falha de
envio, quantos usuários estão sem e-mail cadastrado e o botão de teste.

**Envio não bloqueia o sistema.** Os e-mails saem em segundo plano; se o
servidor estiver fora do ar, a OS é aberta e gravada normalmente e o erro fica
registrado para consulta.

### Central de Relatórios (Excel)

Onze relatórios em `.xlsx`, com filtro de período e atalhos de 30/90/180/365 dias.
Todos saem formatados com a identidade da empresa, cabeçalho fixo, autofiltro,
larguras ajustadas e linha de totais.

| Relatório | Abas |
|---|---|
| Ordens de serviço | dados completos · resumo por situação · por equipamento |
| Apontamentos e tempos | linha do tempo de cada OS · intervalos de trabalho/pausa/almoço/espera |
| Preventivas | OMs · programação 52 semanas · previsto x realizado · por responsável |
| Respostas dos check lists | item a item, com pendências e OS geradas |
| Rondas de inspeção | pontos verificados, respostas e OS geradas |
| Materiais e estoque | saldo · movimentações · alertas com sugestão de compra |
| Solicitações de material | acompanhamento completo · histórico de situações |
| Indicadores | MTBF/MTTR/disponibilidade/custos · produtividade · defeitos e causas |
| Equipamentos | inventário · matriz de criticidade · níveis configurados |
| Manutenção em terceiros | envios, empresas, retornos e valores |
| Usuários e auditoria | cadastro de usuários · log completo *(só administrador)* |

> As planilhas trazem **valores já apurados, nunca fórmulas**. Como são geradas no
> servidor, onde não há Excel para recalcular, uma fórmula chegaria vazia ao usuário.

### Backup

Três caminhos, em *Administração → Backup* (administrador e supervisão):

1. **Excel** — uma aba por tabela mais um índice. Legível por qualquer pessoa,
   serve de registro histórico e auditoria.
2. **JSON** — fidelidade total de tipos, para restaurar por script ou migrar de servidor.
3. **`pg_dump`** — comando documentado na própria tela. É o único que inclui as
   **imagens e anexos** (fotos de ronda, anexos de OS, imagens de material), que ficam
   como binário no banco e não entram nos dois primeiros.

A tela mostra o número de registros por tabela, a contagem de anexos e a data do
último backup. Toda geração fica registrada na auditoria.

Rotina sugerida: `pg_dump` semanal guardado fora do Railway e backup em Excel no
fechamento de cada mês.

### Área do Administrador
Usuários e permissões, equipamentos (com criticidade e ficha técnica), centros
de trabalho, centros de custo, defeitos, causas, estabelecimentos, planos de
preventiva, rondas, manutenção em terceiros, parâmetros do sistema e auditoria.

---

## De onde veio cada funcionalidade

| Documento / planilha atual | Onde está no sistema |
|---|---|
| Formulário “Solicitação de Ordem de Serviço” | `Abrir OS` |
| Planilha “Gestão de Ordens de Serviço” | `Ordens de serviço` + indicadores |
| Formulário “Solicitação de Material” | `Solicitar material` |
| Planilha “Monitoramento de Solicitação de Materiais” | `Solicitações → Acompanhamento` |
| DC-014 · aba PLANEJAMENTO DE PREVENTIVAS | `Grade 52 semanas` |
| DC-014 · aba PLANEJAMENTO DE MATERIAIS | `Plano de materiais` |
| DC-014 · aba ESTOQUE DE SEGURANÇA | Estoque mín./máx. + `Alertas` |
| DC-014 · abas SALDO NLAG e SALDO HIBE-ERSA | `Saldo de estoque` (importador) |
| DC-014 · aba Inventário | `Admin → Equipamentos` (importador) |
| DC-014 · aba Reprogramações | `Preventivas → Reprogramações` |
| DC-014 · aba Manutenções em Terceiros | `Admin → Manutenção terceiros` |
| DC-014 · aba Histórico Corretiva | Ficha do equipamento |
| DC-014 · aba Indicadores | `Indicadores` |
| RQ-346 e demais check lists | `Planos e check lists` |
| Sistema NLAG (Flask existente) | Módulo `Materiais` — integrado |

---

## Perfis de acesso

### O que cada um enxerga

**Manutentor** — tela enxuta, só o que é dele:

```
Painel · Minhas ordens de serviço · Modo tablet · OS de emergência
Minhas preventivas (OMs destinadas a ele) · Minhas rondas · Minhas solicitações
```

Não vê a grade de 52 semanas, os planos, o plano de materiais, a triagem, o
depósito NLAG, o parque fabril, os indicadores nem os relatórios. Dentro da OS
ele pede a peça (com baixa automática no NLAG), aponta o tempo e conclui
anexando fotos, vídeos e relatório.

**Líder** — enxerga tudo e distribui:

```
Triagem de OS → escolhe o manutentor      Ordens de manutenção → destina a OM
Criar e destinar rondas                    Grade 52 semanas · planos · check lists
Plano de materiais · indicadores · relatórios · depósito NLAG
```

**Analista de Materiais** — dono do depósito NLAG completo: saldo, cadastro,
entrada, saída, inventário, histórico, coletor, etiquetas, importações,
exportação e alertas. Mais o tratamento das solicitações e os relatórios.

| Perfil | O que pode fazer |
|---|---|
| **Solicitante de OS** | Abre OS, acompanha apontamentos, aprova/reprova o serviço |
| **Manutentor** | Só as OS, preventivas e rondas **destinadas a ele**. Cronômetro, OS de emergência, pedido de peça e conclusão com evidências. Não planeja nem abre o depósito |
| **Analista de Materiais** | Relatórios + **dono do depósito NLAG** — entradas, saídas, cadastro, inventário, etiquetas, coletor, importações, alertas — e trata as solicitações |
| **Líder de Manutenção** | **Distribui OS, OMs e rondas** + planejamento das 52 semanas, cadastros, estoques mínimos, visto de liberação e depósito |
| **Supervisão** | Visão gerencial completa e relatórios globais |
| **Administrador** | Controle total, incluindo usuários e parâmetros |
| **Visualizador** | Apenas consulta de saldo |

### Pedido de peça — fluxo único

O manutentor **não entra no depósito**. Ele pede a peça de dentro da OS
(código e quantidade) e o sistema decide sozinho:

```
                    ┌─ saldo suficiente no NLAG → baixa na hora, OS segue
manutentor pede ────┼─ saldo parcial            → baixa o que há + solicita o resto
                    └─ sem saldo / sem cadastro → solicitação para o Analista
```

Quando gera solicitação, o analista é notificado na hora, a OS pode ser pausada
como *aguardando peça* e o manutentor é avisado quando o material chega.
Peças HIBE/ERSA sempre viram solicitação, com o saldo do SAP anexado para o analista.

---

## 1. Publicar no GitHub

Instale o [Git](https://git-scm.com/downloads), abra o terminal na pasta do
projeto e execute:

```bash
git init
git add .
git commit -m "Sistema Centralizado de Manutenção - Décio Metalúrgica"
git branch -M main
```

Crie um repositório **privado** em <https://github.com/new>
(sugestão de nome: `sistema-manutencao-decio`) e depois:

```bash
git remote add origin https://github.com/SEU-USUARIO/sistema-manutencao-decio.git
git push -u origin main
```

> O arquivo `.gitignore` já impede o envio de senhas (`.env`) e arquivos temporários.

---

## 2. Publicar no Railway

1. Acesse <https://railway.app> e entre com a conta do GitHub.
2. **New Project → Deploy from GitHub repo** → selecione o repositório.
3. Dentro do projeto: **New → Database → Add PostgreSQL**.
   O Railway cria a variável `DATABASE_URL` e conecta automaticamente.
4. No serviço da aplicação, abra **Variables** e adicione:

   | Variável | Valor |
   |---|---|
   | `SECRET_KEY` | uma chave aleatória longa (veja abaixo) |
   | `APP_USUARIO` | `admin` |
   | `APP_SENHA` | a senha do primeiro administrador |

   Para ativar os e-mails, acrescente também `SMTP_HOST`, `SMTP_PORT`,
   `SMTP_SEGURANCA`, `SMTP_USUARIO`, `SMTP_SENHA`, `SMTP_REMETENTE` e `APP_URL`
   (ver a seção *Notificações por e-mail*).

   Para gerar a `SECRET_KEY`:
   ```bash
   python -c "import secrets; print(secrets.token_hex(32))"
   ```

5. Em **Settings → Networking → Generate Domain** para obter o endereço público.
6. O deploy roda sozinho. O banco é criado e populado automaticamente no primeiro
   start (74 equipamentos do inventário, causas, defeitos, centros de custo e a
   ronda diária padrão já vêm cadastrados).

> **Atenção:** troque a senha do administrador logo após o primeiro acesso,
> em *Perfil → Alterar senha*.

---

## 3. Primeiro acesso e carga inicial

Entre com o usuário e a senha definidos em `APP_USUARIO` / `APP_SENHA`.

Ordem sugerida de configuração:

1. **Admin → Usuários** — cadastre a equipe com os perfis corretos
   (solicitantes, manutentores, Maria como analista, Lourivaldo como líder,
   Miguel na supervisão, Gustavo como administrador).
2. **Admin → Criticidade** — revise os cinco níveis (A–E), seus prazos e cores.
3. **Admin → Equipamentos** — os 74 equipamentos da DC-014 já vêm cadastrados, mas a
   criticidade inicial foi **atribuída por inferência** e precisa ser revista. Use o
   ícone da matriz em cada equipamento para classificar pelos seis critérios; depois
   use *Reclassificar pela matriz* para aplicar em massa.
4. **Materiais → Importar**:
   - *Saldo NLAG* → aba `SALDO NLAG` da DC-014 (salve a aba como `.xlsx`);
   - *Saldo HIBE/ERSA do SAP* → aba `SALDO HIBE-ERSA` ou o export direto do SAP.
   Os nomes das colunas são reconhecidos automaticamente.
5. **Materiais → Cadastro** — defina estoque mínimo/máximo e marque as
   peças críticas (base: aba `ESTOQUE DE SEGURANÇA`).
6. **Preventivas → Planos** — crie um plano por equipamento, cadastre os itens
   do check list (ex.: RQ-346) e os materiais por periodicidade.
7. **Preventivas → Grade** — use *Programar plano* para gerar as 52 semanas
   conforme a periodicidade de cada plano.
8. **Rondas → Cadastro** — ajuste os pontos de verificação diários.

---

## Rodar na sua máquina

Requisitos: Python 3.11+ e PostgreSQL 14+.

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/Mac:
source .venv/bin/activate

pip install -r requirements.txt

# Configure as variáveis (copie .env.example para .env e edite)
set DATABASE_URL=postgresql://postgres:senha@localhost:5432/manutencao
set SECRET_KEY=qualquer-coisa-para-teste
set APP_USUARIO=admin
set APP_SENHA=admin123

python app.py
```

Acesse <http://localhost:5000>.

---

## Estrutura do projeto

```
sistema-manutencao-decio/
├── app.py                    Aplicação Flask (factory, filtros, tratamento de erros)
├── db.py                     Conexão, schema completo e carga inicial
├── auth.py                   Perfis e matriz de permissões
├── mailer.py                 Envio de e-mail (SMTP) e modelos das mensagens
├── email_config.py           Configuração do e-mail, com senha criptografada
├── blueprints/
│   ├── auth_bp.py            Login, perfil, notificações
│   ├── home.py               Painel principal
│   ├── corretivas.py         OS, cronômetro, apontamentos, aprovação
│   ├── preventivas.py        Planos, grade 52 semanas, OMs, plano de materiais
│   ├── rondas.py             Rondas diárias de inspeção
│   ├── materiais.py          NLAG + HIBE/ERSA, etiquetas, coletor, alertas
│   ├── solicitacoes.py       Solicitação e monitoramento de material
│   ├── indicadores.py        MTBF, MTTR, % preventivas, custos, parque fabril
│   ├── admin.py              Usuários, equipamentos, cadastros, parâmetros
│   └── api.py                Endpoints JSON usados pelas telas
├── templates/                49 telas (Jinja2 + Bootstrap 5)
├── static/
│   ├── css/app.css           Identidade visual (verde #28A353 · azul #10477D)
│   └── img/logo_decio.png
├── scripts/
│   └── backup_windows.bat    Backup agendável no Windows
├── requirements.txt
├── Procfile · railway.json · nixpacks.toml · runtime.txt
├── .env.example · .gitignore
└── teste_sistema.py          Teste funcional automatizado (93 verificações)
```

### Banco de dados
27 tabelas em PostgreSQL. O schema é **idempotente**: roda a cada deploy,
cria o que falta e nunca apaga dados existentes.

---

## Backup e manutenção

**Pela tela** — *Administração → Backup* oferece download em Excel e em JSON.

**Backup integral** (inclui senhas, imagens e anexos).

> Use a **`DATABASE_PUBLIC_URL`** do serviço Postgres, não a `DATABASE_URL`.
> A segunda aponta para `postgres.railway.internal`, que só existe dentro da
> rede do Railway e não resolve da sua máquina.

```bash
pg_dump "postgresql://postgres:SENHA@xxxxx.proxy.rlwy.net:12345/railway" -Fc -f backup_manutencao.dump
pg_restore -d "URL_DO_DESTINO" --clean --if-exists backup_manutencao.dump
```

No Windows, use `scripts\backup_windows.bat` — basta editar a URL e a pasta de
destino. Ele nomeia o arquivo com a data, cria a pasta se não existir e apaga
backups com mais de 60 dias. Agende no Agendador de Tarefas para rodar semanalmente.

⚠️ O `pg_restore --clean` **apaga os dados do banco de destino** antes de restaurar.
Nunca aponte para o banco de produção sem ter certeza.

**Atualizar o sistema:** basta dar `git push`. O Railway refaz o deploy e o
schema se atualiza sozinho, sem perder dados.

**Rodar os testes:**

```bash
python teste_sistema.py          # fluxo completo do sistema
python teste_plano_materiais.py  # plano de materiais
python teste_criticidade.py      # níveis e matriz de criticidade
python teste_perfis.py           # permissões e fluxo de pedido de peça
python teste_relatorios.py       # relatórios em Excel e backup
python teste_email.py            # disparos de e-mail
python teste_fluxo.py            # fluxo ponta a ponta com triagem
python teste_email_config.py     # configuração de e-mail pela tela
python teste_recorte.py          # recorte de acesso por perfil
```

---

## Identidade visual

Cores extraídas da logo da empresa:

| Uso | Cor |
|---|---|
| Verde institucional | `#28A353` |
| Azul institucional | `#10477D` |
| Gradiente (cabeçalhos, botões principais) | verde → azul |
| Criticidade | definida em Admin → Criticidade (padrão A–E) |

Fonte **Manrope**. Layout responsivo: sidebar no desktop, menu recolhível no
celular e modo tablet com botões grandes para o chão de fábrica.

---

*Décio Metalúrgica — Manutenção Industrial e Predial · Matriz e Filial*
