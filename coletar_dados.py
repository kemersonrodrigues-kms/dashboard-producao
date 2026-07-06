#!/usr/bin/env python3
"""
coletar_dados.py — GitHub Actions
Baixa o relatório de produção do sistema i4 (Grow Label) e atualiza dados.json.

Nova estrutura: dados.json com histórico mensal preservado.
Credenciais via variáveis de ambiente I4_USUARIO e I4_SENHA (GitHub Secrets).
"""

import os, sys, time, json, shutil, logging, tempfile, traceback
from datetime import datetime, timedelta, date
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

import pandas as pd

# ─────────────────────────────────────────────────────────────
# CONFIGURAÇÕES
# ─────────────────────────────────────────────────────────────
URL_LOGIN     = "https://growlabel.iquattro.com.br"
URL_RELATORIO = "https://growlabel.iquattro.com.br/pcp/relapontamento/list"
USUARIO       = os.environ.get("I4_USUARIO", "")
SENHA         = os.environ.get("I4_SENHA", "")
EMPRESA       = "GROW LABEL"

PASTA_DOWNLOADS = "/tmp/relatorio_download"
HORA_INICIO     = "00:00"
HORA_FIM        = "23:59"

# Mapeamento grupo i4 → aba do dashboard
GRUPO_PARA_ABA = {
    "IMPRESSÃO":        "Flexografia",
    "BRANCA":           "Flexografia",
    "ETIRAMA 250 UV":   "Flexografia",
    "ETIRAMA 350":      "Flexografia",
    "LAMINAÇÃO":        "Flexografia",
    "GERAL":            "Flexografia",
    "DIGITAL":          "Digital",
    "POLLY M370":       "Polly",
    "REVISÃO":          "Revisão",
    "REVISÃO IDEMITSU": "Revisão",
    "BULA":             "Revisão",
    "SLEEVE":           "Outros",
    "CORTE":            "Outros",
    "GUILHOTINA":       "Outros",
    "IMPRESSÃO OFFSET": "Outros",
}

# Exceções por nome exato de máquina
MAQUINA_PARA_ABA = {
    "14 - POLLY M370": "Polly",
    "INSERT 250 01":   "Insert",
    "INSERT 250 02":   "Insert",
    "INSERT 350 03":   "Insert",
}

# Labels dos meses
MESES_PT = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março",   4: "Abril",
    5: "Maio",    6: "Junho",     7: "Julho",    8: "Agosto",
    9: "Setembro",10:"Outubro",  11: "Novembro",12: "Dezembro",
}

# ─────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.info


# ─────────────────────────────────────────────────────────────
# HELPERS SELENIUM
# ─────────────────────────────────────────────────────────────
def calcular_periodo_mes_atual():
    hoje = datetime.now()
    return hoje.replace(day=1).strftime("%d/%m/%Y"), hoje.strftime("%d/%m/%Y")


def limpar_pasta():
    if os.path.exists(PASTA_DOWNLOADS):
        shutil.rmtree(PASTA_DOWNLOADS)
    os.makedirs(PASTA_DOWNLOADS, exist_ok=True)


def snapshot():
    return set(os.listdir(PASTA_DOWNLOADS)) if os.path.exists(PASTA_DOWNLOADS) else set()


def configurar_navegador(headless=True):
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--disable-background-timer-throttling")
    opts.add_argument("--disable-backgrounding-occluded-windows")
    opts.add_argument("--disable-renderer-backgrounding")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--disable-popup-blocking")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument(f"--user-data-dir={tempfile.mkdtemp()}")
    opts.add_argument(
        "--user-agent=Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    opts.add_experimental_option("prefs", {
        "download.default_directory": PASTA_DOWNLOADS,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
    })
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)

    # Selenium 4.6+ (selenium-manager) detecta o ChromeDriver automaticamente
    service = Service()
    driver  = webdriver.Chrome(service=service, options=opts)
    driver.set_page_load_timeout(180)
    driver.execute_cdp_cmd("Browser.setDownloadBehavior", {
        "behavior": "allow", "downloadPath": PASTA_DOWNLOADS
    })
    return driver


def fazer_login(driver):
    log("Fazendo login no i4...")
    driver.get(URL_LOGIN)
    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='text']"))
    ).send_keys(USUARIO)
    driver.find_element(By.CSS_SELECTOR, "input[type='password']").send_keys(SENHA)
    driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located(
            (By.XPATH, "//*[contains(text(),'Dashboard') or contains(text(),'Menu') or contains(text(),'PCP')]")
        )
    )
    log("   Login OK")


def selecionar_empresa(driver):
    try:
        WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, f"//*[contains(text(),'{EMPRESA}')]"))
        ).click()
        time.sleep(2)
        log("   Empresa selecionada")
    except Exception:
        log("   Empresa já selecionada")


def preencher_filtros(driver, data_inicio, data_fim):
    log(f"   Filtros: {data_inicio} → {data_fim}")
    WebDriverWait(driver, 30).until(
        EC.presence_of_element_located((By.ID, "a02_data_de"))
    )
    for field_id, value in [("a02_data_de", data_inicio), ("a02_data_ate", data_fim)]:
        driver.execute_script(f"""
            var el = document.getElementById('{field_id}');
            if (el) {{
                el.value = '{value}';
                el.dispatchEvent(new Event('input',  {{bubbles:true}}));
                el.dispatchEvent(new Event('change', {{bubbles:true}}));
            }}
        """)
        time.sleep(0.3)
    for field_id, value in [("a02_hora_inicio", HORA_INICIO), ("a02_hora_fim", HORA_FIM)]:
        driver.execute_script(f"""
            var el = document.getElementById('{field_id}');
            if (el) {{
                el.value = '{value}';
                el.dispatchEvent(new Event('change', {{bubbles:true}}));
            }}
        """)
        time.sleep(0.2)


def clicar_filtrar(driver):
    log("   Clicando em Filtrar...")
    botao = WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.ID, "action_filter"))
    )
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", botao)
    time.sleep(1)
    driver.execute_script("arguments[0].click();", botao)
    log("   Filtrar clicado!")


def aguardar_e_exportar(driver, timeout=180):
    log("   Aguardando resultado...")
    inicio = time.time()
    cenario = None

    while time.time() - inicio < 20:
        for el in driver.find_elements(By.XPATH, "//button[contains(text(),'Gerar Planilha')]"):
            if el.is_displayed():
                cenario = "popup"; break
        if cenario: break
        time.sleep(1)

    if not cenario:
        log("   Popup não apareceu — verificando tabela...")
        while time.time() - inicio < timeout:
            for btn_id in ("data_tables_buttons4", "data_tables_buttons4_stick"):
                for b in driver.find_elements(By.ID, btn_id):
                    if b.is_displayed() and b.is_enabled():
                        cenario = "tabela"; break
            if cenario: break
            time.sleep(2)

    if not cenario:
        raise Exception("Nenhum método de exportação encontrado.")

    log(f"   Cenário: {cenario.upper()}")

    if cenario == "popup":
        btn = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'Gerar Planilha')]"))
        )
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
        time.sleep(0.5)
        driver.execute_script("arguments[0].click();", btn)
    else:
        time.sleep(5)
        botao_print = None
        for btn_id in ("data_tables_buttons4", "data_tables_buttons4_stick"):
            for el in driver.find_elements(By.ID, btn_id):
                if el.is_displayed() and el.is_enabled():
                    botao_print = el; break
            if botao_print: break
        if botao_print is None:
            raise Exception("Botão de exportação não encontrado.")
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", botao_print)
        time.sleep(0.5)
        driver.execute_script("arguments[0].click();", botao_print)
        time.sleep(1)
        item = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//li[contains(.,'Exportar para Excel')]"))
        )
        driver.execute_script("arguments[0].click();", item)

    log("   Exportação iniciada!")


def aguardar_download(snap_inicial, timeout=180):
    log("   Aguardando download...")
    inicio = time.time()
    while time.time() - inicio < timeout:
        arquivos = set(os.listdir(PASTA_DOWNLOADS))
        novos = arquivos - snap_inicial
        em_andamento = [f for f in novos if f.lower().endswith(('.crdownload', '.tmp', '.partial'))]
        excel_novos  = [f for f in novos if f.lower().endswith(('.xlsx', '.xls')) and not f.startswith('~')]
        if em_andamento:
            log(f"   Download em andamento... ({int(time.time()-inicio)}s)")
            time.sleep(3)
            continue
        if excel_novos:
            caminho = os.path.join(PASTA_DOWNLOADS, max(
                excel_novos,
                key=lambda x: os.path.getmtime(os.path.join(PASTA_DOWNLOADS, x))
            ))
            time.sleep(2)
            log(f"   Download concluído: {os.path.basename(caminho)}")
            return caminho
        time.sleep(2)
    log("   TIMEOUT esperando download!")
    return None


def validar_excel(caminho):
    tamanho_kb = os.path.getsize(caminho) / 1024
    log(f"   Tamanho: {tamanho_kb:.1f} KB")
    if tamanho_kb < 8.5:
        log("   AVISO: arquivo muito pequeno!")
        return False
    return True


# ─────────────────────────────────────────────────────────────
# PROCESSAMENTO: Excel → estrutura de mês
# ─────────────────────────────────────────────────────────────
def extrair_hora(val):
    if val is None: return 12
    try:
        if hasattr(val, 'hour'): return val.hour
        s = str(val)
        if ' ' in s: s = s.split(' ')[-1]
        if ':' in s: return int(s.split(':')[0])
    except Exception:
        pass
    return 12


def calc_data_turno(data_val, hora_val):
    hora = extrair_hora(hora_val)
    if isinstance(data_val, datetime):
        d = data_val.date()
    elif isinstance(data_val, date):
        d = data_val
    else:
        try:
            d = pd.to_datetime(str(data_val)).date()
        except Exception:
            return date.today()
    return d - timedelta(days=1) if 0 <= hora < 7 else d


def get_aba(grupo, maquina):
    maq = str(maquina).strip()
    if maq in MAQUINA_PARA_ABA:
        return MAQUINA_PARA_ABA[maq]
    grp = str(grupo).strip().upper() if grupo else ""
    return GRUPO_PARA_ABA.get(grp, "Outros")


def get_meta_mh(maquina, config_maquinas):
    maq = str(maquina).strip()
    if maq in config_maquinas:
        return config_maquinas[maq].get("meta_mh", 1500)
    for nome, conf in config_maquinas.items():
        if nome in maq or maq in nome:
            return conf.get("meta_mh", 1500)
    return 1500


def processar_excel(caminho_excel, config):
    log(f"\nProcessando: {os.path.basename(caminho_excel)}")
    df = pd.read_excel(caminho_excel, engine='openpyxl')
    log(f"   {len(df)} linhas, {len(df.columns)} colunas")

    COL_MAQUINA  = "Máquina"
    COL_OPERACAO = "Operação"
    COL_DATA     = "Data de Início"
    COL_HORA     = "Hora de Início"
    COL_METROS   = "Metros Lineares"
    COL_MINUTOS  = "Total Minutos"
    COL_GRUPO    = "Grupo de Máquinas"

    colunas = set(df.columns)

    # Filtrar somente PRODUÇÃO (002) — não inclui revisão para métricas de metros
    if COL_OPERACAO in colunas:
        mask = df[COL_OPERACAO].fillna("").str.startswith("002")
        df_prod = df[mask].copy()
    else:
        df_prod = df.copy()
    log(f"   Linhas de produção: {len(df_prod)}")

    if len(df_prod) == 0:
        log("   AVISO: nenhuma linha de produção!")
        df_prod = df.copy()

    # Data turno
    if COL_DATA in colunas and COL_HORA in colunas:
        df_prod["_data_turno"] = [
            calc_data_turno(row[COL_DATA], row[COL_HORA])
            for _, row in df_prod.iterrows()
        ]
    elif COL_DATA in colunas:
        df_prod["_data_turno"] = pd.to_datetime(df_prod[COL_DATA], errors="coerce").dt.date
    else:
        df_prod["_data_turno"] = date.today()

    # Aba do dashboard
    if COL_GRUPO in colunas and COL_MAQUINA in colunas:
        df_prod["_aba"] = [
            get_aba(row.get(COL_GRUPO, ""), row.get(COL_MAQUINA, ""))
            for _, row in df_prod.iterrows()
        ]
    elif COL_MAQUINA in colunas:
        df_prod["_aba"] = df_prod[COL_MAQUINA].apply(lambda m: get_aba("", m))
    else:
        df_prod["_aba"] = "Outros"

    def to_float(val):
        try:
            v = float(val)
            return v if pd.notna(v) and v >= 0 else 0.0
        except Exception:
            return 0.0

    df_prod["_metros"]  = df_prod[COL_METROS].apply(to_float)  if COL_METROS  in colunas else 0.0
    df_prod["_minutos"] = df_prod[COL_MINUTOS].apply(to_float) if COL_MINUTOS in colunas else 0.0

    # Filtrar apenas metros > 0
    df_prod = df_prod[df_prod["_metros"] > 0]

    config_maquinas = {m["maquina"]: m for m in config.get("maquinas", [])}

    ABAS = ["Geral", "Flexografia", "Digital", "Polly", "Insert", "Revisão"]

    # Datas disponíveis (para por_dia das máquinas)
    todas_datas = sorted(df_prod["_data_turno"].dropna().unique())
    todas_datas_str = [d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d) for d in todas_datas]

    resultado_abas = {}

    for aba in ABAS:
        df_aba = df_prod if aba == "Geral" else df_prod[df_prod["_aba"] == aba]

        # Por máquina
        por_maquina = []
        if COL_MAQUINA in colunas and len(df_aba):
            for maq, grp in df_aba.groupby(COL_MAQUINA, sort=False):
                m_metros  = grp["_metros"].sum()
                m_minutos = grp["_minutos"].sum()
                meta      = get_meta_mh(maq, config_maquinas)
                mh        = (m_metros / m_minutos * 60) if m_minutos > 0 else 0
                meta_metros = round(meta * m_minutos / 60)
                perf_pct  = round((m_metros / meta_metros * 100) if meta_metros > 0 else 0, 1)

                # Por dia desta máquina
                por_dia_maq = []
                maq_por_dia = grp.groupby("_data_turno")["_metros"].sum()
                for dt_str in todas_datas_str:
                    try:
                        dt_key = date.fromisoformat(dt_str)
                    except Exception:
                        dt_key = dt_str
                    metros_dia = float(maq_por_dia.get(dt_key, 0))
                    por_dia_maq.append({"data": dt_str, "metros": round(metros_dia)})

                por_maquina.append({
                    "maquina":         str(maq),
                    "metros":          round(m_metros),
                    "minutos":         round(m_minutos),
                    "mh":              round(mh),
                    "meta_mh":         meta,
                    "meta_metros":     meta_metros,
                    "performance_pct": perf_pct,
                    "por_dia":         por_dia_maq
                })
            por_maquina.sort(key=lambda x: x["metros"], reverse=True)

        # Por dia (aba total)
        por_dia = []
        if len(df_aba):
            for dia, grp_dia in df_aba.groupby("_data_turno", sort=True):
                label = dia.strftime("%Y-%m-%d") if hasattr(dia, "strftime") else str(dia)
                por_dia.append({
                    "data":   label,
                    "metros": round(grp_dia["_metros"].sum())
                })

        total_metros  = df_aba["_metros"].sum()
        total_minutos = df_aba["_minutos"].sum()
        mh_medio      = (total_metros / total_minutos * 60) if total_minutos > 0 else 0
        meta_total    = sum(p["meta_metros"] for p in por_maquina)
        perf_geral    = round((total_metros / meta_total * 100) if meta_total > 0 else 0, 1)

        resultado_abas[aba] = {
            "total_metros":    round(total_metros),
            "total_minutos":   round(total_minutos),
            "mh_medio":        round(mh_medio),
            "meta_total":      round(meta_total),
            "performance_pct": perf_geral,
            "por_dia":         por_dia,
            "por_maquina":     por_maquina
        }

    # Período
    datas_validas = df_prod["_data_turno"].dropna()
    periodo_ini = periodo_fim = ""
    if len(datas_validas):
        dmin = min(datas_validas)
        dmax = max(datas_validas)
        periodo_ini = dmin.strftime("%d/%m/%Y") if hasattr(dmin, "strftime") else str(dmin)
        periodo_fim = dmax.strftime("%d/%m/%Y") if hasattr(dmax, "strftime") else str(dmax)

    return {
        "periodo": {"inicio": periodo_ini, "fim": periodo_fim},
        "abas":    resultado_abas
    }


# ─────────────────────────────────────────────────────────────
# FLUXO PRINCIPAL
# ─────────────────────────────────────────────────────────────
def baixar_excel():
    if not USUARIO or not SENHA:
        log("ERRO: Defina as env vars I4_USUARIO e I4_SENHA!")
        sys.exit(1)

    data_inicio, data_fim = calcular_periodo_mes_atual()
    log(f"Período: {data_inicio} → {data_fim}")
    limpar_pasta()

    for headless in [True, False]:
        modo = "headless" if headless else "janela visível (fallback)"
        log(f"\nModo: {modo}")
        driver = None
        try:
            driver = configurar_navegador(headless=headless)
            fazer_login(driver)
            selecionar_empresa(driver)
            snap = snapshot()

            log("Acessando relatório...")
            driver.get(URL_RELATORIO)
            time.sleep(5)

            preencher_filtros(driver, data_inicio, data_fim)
            clicar_filtrar(driver)
            aguardar_e_exportar(driver)

            caminho = aguardar_download(snap)
            if caminho and validar_excel(caminho):
                return caminho
            log("   Arquivo inválido — tentando novamente...")
        except Exception as e:
            log(f"   Erro ({modo}): {e}")
            traceback.print_exc()
        finally:
            if driver:
                try: driver.quit()
                except Exception: pass
    return None


def main():
    log("=" * 60)
    log("  GROW LABEL — Coletor de Dados (GitHub Actions)")
    log(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log("=" * 60)

    # Config
    config_path = Path(__file__).parent / "config.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        log(f"Config: {len(config.get('maquinas',[]))} máquinas")
    except Exception as e:
        log(f"AVISO config.json: {e}")
        config = {"maquinas": []}

    # Download
    caminho_excel = baixar_excel()
    if not caminho_excel:
        log("\nERRO: Não foi possível baixar o relatório!")
        sys.exit(1)

    # Processar
    try:
        novo_mes = processar_excel(caminho_excel, config)
    except Exception as e:
        log(f"\nERRO ao processar Excel: {e}")
        traceback.print_exc()
        sys.exit(1)

    # ── Carregar dados.json existente (preservar histórico) ──
    output = Path(__file__).parent / "dados.json"
    if output.exists():
        try:
            dados = json.loads(output.read_text(encoding="utf-8"))
        except Exception:
            dados = {"meses": {}, "meses_disponiveis": []}
    else:
        dados = {"meses": {}, "meses_disponiveis": []}

    if "meses" not in dados:
        dados["meses"] = {}

    # Chave do mês atual (YYYY-MM)
    hoje = datetime.now()
    chave_mes = hoje.strftime("%Y-%m")
    label_mes = f"{MESES_PT[hoje.month]} {hoje.year}"

    # Atualizar apenas o mês atual
    dados["meses"][chave_mes] = {
        "label":   label_mes,
        **novo_mes
    }

    # Atualizar lista de meses disponíveis
    dados["meses_disponiveis"] = sorted(dados["meses"].keys())
    dados["ultima_atualizacao"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    # Salvar
    output.write_text(
        json.dumps(dados, ensure_ascii=False, separators=(',', ':'), default=str),
        encoding="utf-8"
    )

    log(f"\n✅ dados.json atualizado!")
    log(f"   Mês atual: {chave_mes} ({label_mes})")
    log(f"   Meses disponíveis: {dados['meses_disponiveis']}")
    log(f"   Atualização: {dados['ultima_atualizacao']}")

    aba_geral = novo_mes["abas"].get("Geral", {})
    log(f"   Total metros: {aba_geral.get('total_metros',0):>10,.0f} M/L")
    log(f"   Meta total:   {aba_geral.get('meta_total',0):>10,.0f} M/L")
    log(f"   Perfo
