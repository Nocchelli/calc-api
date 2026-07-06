from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from app.enums import ContratanteEnum
from app.extractors.viver_bem import ExtratorViverBem
from app.extractors.real import ExtratorReal
from app.extractors.lee_empreendimentos import ExtratorLeeEmpreendimentos

app = FastAPI(title="Motor de Cálculo e Extração")

# --- BLOCO DE SEGURANÇA CORS OBRIGATÓRIO ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# ------------------------------------------

@app.get("/")
def home():
    return {"status": "Serviço estruturado rodando com sucesso!"}

@app.post("/extrair-parcelas")
async def extrair_parcelas(
    contratante: ContratanteEnum = Form(..., description="1 = VIVER_BEM, 2 = REAL, 3 = LEE_EMPREENDIMENTOS"),
    file: UploadFile = File(...)
):
    conteudo_arquivo = await file.read()

    # Fábrica de extratores: Escolhe a regra baseado no parâmetro enviado
    if contratante == ContratanteEnum.VIVER_BEM:
        extrator = ExtratorViverBem()
    elif contratante == ContratanteEnum.REAL:
        extrator = ExtratorReal()
    elif contratante == ContratanteEnum.LEE_EMPREENDIMENTOS:
        extrator = ExtratorLeeEmpreendimentos()
    else:
        return {"erro": "Contratante inválido."}

    # Executa a extração
    dados_extraidos = extrator.extrair(conteudo_arquivo)

    if "erro" in dados_extraidos:
        return {"erro": dados_extraidos["erro"]}

    return {
        "cliente": dados_extraidos.get("cliente"),
        "contrato": dados_extraidos.get("contrato"),
        "parcelas": dados_extraidos.get("parcelas", [])
    }