import json
import os
import time
import re
import unicodedata
import pandas as pd
from PIL import Image
import streamlit as st
from google import genai
from google.genai import types

st.set_page_config(page_title="Auditoria de Canhotas v1.0", layout="wide")
st.title("🛡️ Painel de Auditoria Operacional e Grafotécnica")

api_key = st.secrets.get("GEMINI_API_KEY")
if not api_key:
    st.error("Chave GEMINI_API_KEY não encontrada nos Secrets do Streamlit.")
    st.stop()

client = genai.Client(api_key=api_key)

COLABORADORES_CADASTRADOS = [
    "ALDO GOMES",
    "RICARDO TEIXEIRA",
    "CLEITON"
]

def normalizar_nome_arquivo(texto):
    if not texto:
        return "DESCONHECIDO"
    texto_norm = unicodedata.normalize('NFD', texto).encode('ascii', 'ignore').decode("utf-8")
    texto_limpo = re.sub(r'[^A-Z0-9 ]', '', texto_norm.upper().strip())
    return texto_limpo.replace(" ", "_")

def identificar_colaborador(nome_lido):
    nome_norm = normalizar_nome_arquivo(nome_lido).replace("_", " ")
    for colab in COLABORADORES_CADASTRADOS:
        if colab in nome_norm or nome_norm in colab:
            return colab
    return None

def limpar_e_parsear_json(texto_resposta):
    if not texto_resposta:
        raise ValueError("A resposta da API retornou vazia.")
    texto_limpo = re.sub(r'^```json\s*', '', texto_resposta.strip(), flags=re.MULTILINE)
    texto_limpo = re.sub(r'^```\s*', '', texto_limpo, flags=re.MULTILINE)
    texto_limpo = re.sub(r'```$', '', texto_limpo, flags=re.MULTILINE).strip()
    return json.loads(texto_limpo)

def obter_modelos_candidatos(client):
    modelos_prioritarios = [
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
        "gemini-1.5-flash",
        "gemini-1.5-pro"
    ]
    modelos_detectados = []
    try:
        for m in client.models.list():
            nome = getattr(m, 'name', '') or getattr(m, 'model_id', '')
            nome_limpo = nome.replace('models/', '')
            methods = getattr(m, 'supported_generation_methods', []) or getattr(m, 'supported_actions', [])
            if not methods or 'generateContent' in str(methods):
                if nome_limpo and 'gemini' in nome_limpo and nome_limpo not in modelos_detectados:
                    modelos_detectados.append(nome_limpo)
    except Exception:
        pass

    lista_final = modelos_detectados + [m for m in modelos_prioritarios if m not in modelos_detectados]
    return lista_final if lista_final else modelos_prioritarios

def processar_com_fallback(client, prompt, contents):
    modelos_candidatos = obter_modelos_candidatos(client)
    erros = []

    for modelo in modelos_candidatos:
        for tentativa in range(2):
            try:
                response = client.models.generate_content(
                    model=modelo,
                    contents=[prompt] + contents,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json"
                    ),
                )
                return limpar_e_parsear_json(response.text)
            except Exception as err:
                msg = str(err)
                erros.append(f"[{modelo}] {msg}")
                if any(code in msg for code in ["503", "429", "RESOURCE_EXHAUSTED"]):
                    time.sleep(2 * (tentativa + 1))
                    continue
                if "404" in msg or "NOT_FOUND" in msg:
                    break
                break
    raise RuntimeError("Falha na comunicação com a API Gemini:\n" + "\n".join(erros))

# Interface de Entrada
col1, col2 = st.columns(2)
with col1:
    canhotas_files = st.file_uploader("1. Lote de Canhotas (PDF ou Imagem)", type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True)
with col2:
    fatura_file = st.file_uploader("2. Fatura / Relatório Mensal", type=["pdf", "png", "jpg", "jpeg"])

if st.button("🚀 Executar Auditoria", type="primary"):
    if not canhotas_files or not fatura_file:
        st.warning("Envie as canhotas e a fatura para iniciar.")
    else:
        try:
            # Etapa 1: Leitura da Fatura
            with st.spinner("Analisando Fatura e extraindo totais declarados..."):
                fatura_file.seek(0)
                if fatura_file.type == "application/pdf":
                    fatura_contents = [types.Part.from_bytes(data=fatura_file.read(), mime_type="application/pdf")]
                else:
                    fatura_contents = [Image.open(fatura_file)]

                prompt_fatura = """
                Extraia a quantidade total de itens/serviços prestados constantes na fatura no formato JSON:
                {
                    "quantidade_total_faturada": 0,
                    "valor_total_fatura": 0.0
                }
                """
                dados_fatura = processar_com_fallback(client, prompt_fatura, fatura_contents)
                total_exigido_fatura = int(dados_fatura.get("quantidade_total_faturada", 0))

            # Etapa 2: Leitura e Perícia das Canhotas
            with st.spinner(f"Auditando lote de {len(canhotas_files)} canhotas..."):
                 canhotas_processadas = []

                for c_file in canhotas_files:
                    c_file.seek(0)
                    if c_file.type == "application/pdf":
                        c_contents = [types.Part.from_bytes(data=c_file.read(), mime_type="application/pdf")]
                        imagem_preview = None
                    else:
                        img = Image.open(c_file)
                        c_contents = [img]
                        imagem_preview = img

                    prompt_leitura = """
                    Analise esta canhota e extraia:
                    {
                        "colaborador": "NOME COMPLETO LIDO no papel ou N/A",
                        "quantidade": 1
                    }
                    """
                    res_leitura = processar_com_fallback(client, prompt_leitura, c_contents)
                    
                    nome_lido = res_leitura.get("colaborador", "N/A")
                    qtd_item = int(res_leitura.get("quantidade", 1))
                    colaborador_oficial = identificar_colaborador(nome_lido)

                    status_grafotecnico = "SEM_GABARITO"
                    detalhe_grafotecnico = "Sem imagem de referência na pasta /gabaritos."
                    gabarito_path = None

                    if colaborador_oficial:
                        chave_arquivo = normalizar_nome_arquivo(colaborador_oficial)
                        path_tentativa = f"gabaritos/{chave_arquivo}.jpg"

                        if os.path.exists(path_tentativa):
                            gabarito_path = path_tentativa
                            gabarito_img = Image.open(gabarito_path)

                            prompt_grafotecnico = """
                            Compare a ASSINATURA do documento com a ASSINATURA DO GABARITO DE REFERÊNCIA.
                            Responda em JSON estrito:
                            {
                                "resultado_assinatura": "CONFORME" ou "SUSPEITA_DIVERGENCIA" ou "AUSENTE",
                                "justificativa": "resumo direto dos traços e semelhança observada"
                            }
                            """
                            res_grafo = processar_com_fallback(client, prompt_grafotecnico, c_contents + [gabarito_img])
                            status_grafotecnico = res_grafo.get("resultado_assinatura", "SUSPEITA_DIVERGENCIA")
                            detalhe_grafotecnico = res_grafo.get("justificativa", "")

                    canhotas_processadas.append({
                        "nome_arquivo": c_file.name,
                        "colaborador_lido": nome_lido,
                        "colaborador_oficial": colaborador_oficial if colaborador_oficial else "NÃO CADASTRADO",
                        "quantidade": qtd_item,
                        "status_grafotecnico": status_grafotecnico,
                        "detalhe_grafotecnico": detalhe_grafotecnico,
                        "imagem_original": imagem_preview,
                        "gabarito_path": gabarito_path
                    })

            # Consolidated Metrics Calculation
            total_auditado = sum(item["quantidade"] for item in canhotas_processadas if item["status_grafotecnico"] == "CONFORME")
            diferenca = total_auditado - total_exigido_fatura

            st.divider()
            st.subheader("📊 Painel de Conciliação Financeira e Operacional")

            m1, m2, m3 = st.columns(3)
            m1.metric("Total Exigido (Fatura)", f"{total_exigido_fatura} un")
            m2.metric("Total Validadas (Canhotas)", f"{total_auditado} un", delta=f"{diferenca} un vs Fatura")
            
            if diferenca == 0:
                m3.success("✅ AUDITORIA CONFORMADA (100% BATEU)")
            elif diferenca > 0:
                m3.warning(f"⚠️ ATENÇÃO: Excesso de {diferenca} itens nas canhotas.")
            else:
                m3.error(f"❌ DIVERGÊNCIA: Faltam {abs(diferenca)} itens comprovados.")

            st.divider()
            st.subheader("👥 Relação de Colaboradores Validados")

            # Grouping by Collaborator
            df_canhotas = pd.DataFrame(canhotas_processadas)
            
            for colab, group in df_canhotas.groupby("colaborador_oficial"):
                qtd_total_colab = group["quantidade"].sum()
                status_geral = "CONFORME" if all(s == "CONFORME" for s in group["status_grafotecnico"]) else "SUSPEITO"
                
                c_nome, c_qtd, c_status, c_acao = st.columns([3, 2, 2, 2])
                
                c_nome.write(f"**{colab}**")
                c_qtd.write(f"**{qtd_total_colab}** item(ns)")
                
                if status_geral == "CONFORME":
                    c_status.markdown("🟢 **Validações OK**")
                else:
                    c_status.markdown("🔴 **Requer Atenção**")

                with c_acao:
                    with st.popover("🔍 Ver Evidências"):
                        st.markdown(f"### Detalhes de Auditoria - {colab}")
                        for idx, row in group.iterrows():
                            st.write(f"**Canhota:** `{row['nome_arquivo']}` | **Qtd:** {row['quantidade']}")
                            st.caption(f"**Parecer:** {row['status_grafotecnico']} - {row['detalhe_grafotecnico']}")
                            
                            sub_col1, sub_col2 = st.columns(2)
                            with sub_col1:
                                if row["imagem_original"]:
                                    st.image(row["imagem_original"], caption="Canhota Processada", use_container_width=True)
                                else:
                                    st.info("Documento em formato PDF.")
                            with sub_col2:
                                if row["gabarito_path"]:
                                    st.image(row["gabarito_path"], caption="Gabarito de Assinatura", use_container_width=True)
                                else:
                                    st.warning("Gabarito não cadastrado.")
                            st.divider()

        except Exception as e:
            st.error("Erro durante a execução da auditoria:")
            st.code(str(e))
