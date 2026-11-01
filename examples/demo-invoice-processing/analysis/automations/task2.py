import os, requests, msal
TENANT=os.getenv('TENANT_ID'); CLIENT=os.getenv('CLIENT_ID'); SECRET=os.getenv('CLIENT_SECRET')
DI_ENDPOINT=os.getenv('DI_ENDPOINT'); DI_KEY=os.getenv('DI_KEY')
SITE_ID=os.getenv('SITE_ID'); DRIVE_ID=os.getenv('DRIVE_ID'); FILE_ID=os.getenv('AP_TRACKER_FILE_ID')
# 1. auth to Microsoft Graph
app=msal.ConfidentialClientApplication(CLIENT, authority=f'https://login.microsoftonline.com/{TENANT}', client_credential=SECRET)
tok=app.acquire_token_for_client(scopes=['https://graph.microsoft.com/.default'])['access_token']
gh={'Authorization': f'Bearer {tok}'}
# 2. find unread vendor invoice emails with PDF
msgs=requests.get('https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?$filter=isRead eq false&$top=10&$expand=attachments', headers=gh).json().get('value',[])
for m in msgs:
    pdfs=[a for a in m.get('attachments',[]) if a.get('name','').lower().endswith('.pdf')]
    if not pdfs: continue
    att_id=pdfs[0]['id']; msg_id=m['id']
    pdf_bytes=requests.get(f'https://graph.microsoft.com/v1.0/me/messages/{msg_id}/attachments/{att_id}/$value', headers=gh).content
    # 3. Azure Document Intelligence prebuilt-invoice
    url=f'{DI_ENDPOINT}/formrecognizer/documentModels/prebuilt-invoice:analyze?api-version=2023-07-31'
    h={'Ocp-Apim-Subscription-Key': DI_KEY, 'Content-Type':'application/pdf'}
    op=requests.post(url, headers=h, data=pdf_bytes).headers['Operation-Location']
    import time; res=None
    for _ in range(20):
        time.sleep(2); res=requests.get(op, headers={'Ocp-Apim-Subscription-Key': DI_KEY}).json()
        if res.get('status') in ('succeeded','failed'): break
    doc=res['analyzeResult']['documents'][0]['fields']
    inv_no=doc.get('InvoiceId',{}).get('valueString','')
    vendor=doc.get('VendorName',{}).get('valueString','')
    total=doc.get('InvoiceTotal',{}).get('valueCurrency',{}).get('amount')
    due=doc.get('DueDate',{}).get('valueDate','')  # needs human rule if missing
    conf=min([doc.get('InvoiceId',{}).get('confidence',0), doc.get('VendorName',{}).get('confidence',0)])
    if conf<0.85 or not total:
        print(f'HOLD {inv_no} low confidence -> route to human'); continue
    payload={'invoice_number':inv_no,'vendor':vendor,'amount':total,'due_date':due}  # single capture for Excel+Portal
    print(payload)  # 4. write to Excel via Graph + submit to portal via Playwright/API in next stage
    # Example Excel append (table): requests.post(f'https://graph.microsoft.com/v1.0/drives/{DRIVE_ID}/items/{FILE_ID}/workbook/tables/APTable/rows/add', headers={**gh,'Content-Type':'application/json'}, json={'values':[[inv_no,vendor,total,due,'']]} )
    # 5. archive email
    requests.post(f'https://graph.microsoft.com/v1.0/me/messages/{msg_id}/move', headers={**gh,'Content-Type':'application/json'}, json={'destinationId':'Processed'})
