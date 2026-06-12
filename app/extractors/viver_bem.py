import pdfplumber
import io
import re
from datetime import datetime
from app.extractors.base import ExtratorBase

class ExtratorViverBem(ExtratorBase):
    def extrair(self, conteudo_arquivo: bytes) -> dict: # 1. Agora retorna um dict
        parcelas_abertas = []
        nome_cliente = "Não identificado"
        detalhes_contrato = {
            "cliente": "Não identificado",
            "venda": "",
            "empreendimento": "",
            "telefones": "",
            "endereco": "",
            "cidade_uf": ""
        }
        
        # Regex para cada campo novo
        padrao_cliente = re.compile(r'Cliente\s*:\s*\d+\s*-\s*(.+)')
        padrao_venda = re.compile(r'Venda:\s*(\d+)')
        padrao_empreend = re.compile(r'Empreend\.:\s*(.+)')
        padrao_fones = re.compile(r'Fones:\s*Residencial:\s*(.*?)\s*Celular:\s*(.*?)\s*Comercial:')
        padrao_end = re.compile(r'End\.:\s*(.+?)\s+No\.')
        padrao_cidade = re.compile(r'Cidade\s*:\s*(.+?)\s+UF\s*:\s*(.+?)\s+CEP')

        padrao_linha_parcela = re.compile(r'^(P\.\d+|E\.\d+)\/\d+')
        padrao_data = re.compile(r'^\d{2}/\d{2}/\d{4}$')
        
        # 2. Regex para capturar tudo que vier depois do "Cliente : numero - "
        padrao_cliente = re.compile(r'Cliente\s*:\s*\d+\s*-\s*(.+)')

        # Pega a data atual do sistema onde o Python está rodando
        hoje = datetime.now().date()

        with pdfplumber.open(io.BytesIO(conteudo_arquivo)) as pdf:
            for pagina in pdf.pages:

                texto = pagina.extract_text()
                if not texto: continue
                
                # Extrair dados do cabeçalho
                m_cli = padrao_cliente.search(texto)
                if m_cli: detalhes_contrato["cliente"] = m_cli.group(1).strip()
                
                m_ven = padrao_venda.search(texto)
                if m_ven: detalhes_contrato["venda"] = m_ven.group(1).strip()
                
                m_emp = padrao_empreend.search(texto)
                if m_emp: detalhes_contrato["empreendimento"] = m_emp.group(1).strip()
                
                m_fon = padrao_fones.search(texto)
                if m_fon: detalhes_contrato["telefones"] = f"Res: {m_fon.group(1)} | Cel: {m_fon.group(2)}"
                
                m_end = padrao_end.search(texto)
                if m_end: detalhes_contrato["endereco"] = m_end.group(1).strip()
                
                m_cid = padrao_cidade.search(texto)
                if m_cid: detalhes_contrato["cidade_uf"] = f"{m_cid.group(1)} / {m_cid.group(2)}"

                texto = pagina.extract_text()
                if not texto:
                    continue
                
                # 3. Tenta encontrar o nome do cliente (geralmente está na página 1)
                if nome_cliente == "Não identificado":
                    match_cliente = padrao_cliente.search(texto)
                    if match_cliente:
                        # Extrai apenas o nome e tira espaços em branco nas pontas
                        nome_cliente = match_cliente.group(1).strip()
                        
                linhas = texto.split("\n")
                for linha in linhas:
                    linha = linha.strip()
                    if padrao_linha_parcela.match(linha):
                        partes = linha.split()
                        try:
                            codigo_parcela = partes[0]
                            data_vencimento = partes[2]
                            valor_original_str = partes[3]
                            dt_recebimento_suspeita = partes[4]
                            
                            if padrao_data.match(dt_recebimento_suspeita):
                                continue
                                
                            valor_limpo = valor_original_str.replace(".", "").replace(",", ".")
                            valor_original_float = float(valor_limpo)
                            
                            # 4. LÓGICA DO STATUS DINÂMICO
                            # Converte a string "DD/MM/YYYY" em uma Data real do Python
                            vencimento_date = datetime.strptime(data_vencimento, "%d/%m/%Y").date()
                            
                            if vencimento_date < hoje:
                                status_parcela = "Vencido"
                            else:
                                status_parcela = "A vencer"
                            
                            parcelas_abertas.append({
                                "parcela": codigo_parcela,
                                "vencimento": data_vencimento,
                                "valor_original": valor_original_float,
                                "status": status_parcela
                            })
                        except (IndexError, ValueError):
                            continue
                            
        return {"contrato": detalhes_contrato, "parcelas": parcelas_abertas, "cliente": detalhes_contrato["cliente"]}