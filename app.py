import streamlit as st
import pandas as pd
import json
import io
import time
from agency_finder.core import lookup_agency
from agency_finder.config import Config
from agency_finder.search import last_search_error as _last_search_error

# Configure Page
st.set_page_config(
    page_title="Italy Web Agency Intelligence",
    page_icon="🇮🇹",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom Sleek Styling (CSS injection for premium look)
st.markdown("""
<style>
    /* Gradient headers and professional typography */
    h1, h2, h3 {
        font-family: 'Outfit', 'Inter', sans-serif;
        font-weight: 700;
        color: #1E293B;
    }
    .main-title {
        background: linear-gradient(135deg, #0A58CA, #0D6EFD, #00C6FF);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        font-size: 2.8rem;
        margin-bottom: 0.2rem;
    }
    .subtitle {
        color: #64748B;
        font-size: 1.1rem;
        margin-bottom: 2rem;
    }
    /* Cards and container borders */
    div[data-testid="stContainer"] {
        border-radius: 12px;
        box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.05), 0 2px 4px -2px rgb(0 0 0 / 0.05);
        padding: 1.5rem;
        background-color: #FFFFFF;
        margin-bottom: 1rem;
    }
    /* Badge styling */
    .badge-vies-valid {
        background-color: #E2FBE9;
        color: #0F5132;
        border: 1px solid #BADBCC;
        padding: 4px 10px;
        border-radius: 20px;
        font-size: 0.85rem;
        font-weight: 600;
        display: inline-block;
    }
    .badge-vies-invalid {
        background-color: #FDF1F2;
        color: #842029;
        border: 1px solid #F5C2C7;
        padding: 4px 10px;
        border-radius: 20px;
        font-size: 0.85rem;
        font-weight: 600;
        display: inline-block;
    }
    .badge-payment-yes {
        background-color: #E0F2FE;
        color: #0369A1;
        border: 1px solid #BAE6FD;
        padding: 6px 12px;
        border-radius: 8px;
        font-size: 0.9rem;
        font-weight: 600;
        display: inline-block;
    }
    .badge-payment-no {
        background-color: #F1F5F9;
        color: #475569;
        border: 1px solid #E2E8F0;
        padding: 6px 12px;
        border-radius: 8px;
        font-size: 0.9rem;
        font-weight: 600;
        display: inline-block;
    }
    .provider-tag {
        background-color: #F3E8FF;
        color: #6B21A8;
        padding: 2px 8px;
        border-radius: 4px;
        font-size: 0.8rem;
        font-weight: 600;
        margin-right: 4px;
        display: inline-block;
    }
</style>
""", unsafe_allow_html=True)

# Title Header
st.markdown('<h1 class="main-title">Italy Web Agency Intelligence Tool</h1>', unsafe_allow_html=True)
st.markdown('<p class="subtitle">Search, validate, and extract data about Italian web agencies and system integrators</p>', unsafe_allow_html=True)

# Sidebar Configuration
st.sidebar.header("🛠️ Settings & API Keys")

search_engine_opt = st.sidebar.selectbox(
    "Search Engine API",
    options=["DuckDuckGo (Free)", "SerpAPI (Google)", "Google Custom Search"],
    index=0
)

# Map UI option to config key
engine_map = {
    "DuckDuckGo (Free)": "duckduckgo",
    "SerpAPI (Google)": "serpapi",
    "Google Custom Search": "google"
}
Config.SEARCH_ENGINE = engine_map[search_engine_opt]

if Config.SEARCH_ENGINE == "serpapi":
    Config.SERPAPI_KEY = st.sidebar.text_input("SerpAPI API Key", type="password", value=Config.SERPAPI_KEY)
elif Config.SEARCH_ENGINE == "google":
    Config.GOOGLE_API_KEY = st.sidebar.text_input("Google API Key", type="password", value=Config.GOOGLE_API_KEY)
    Config.GOOGLE_CX = st.sidebar.text_input("Google Search CX / Engine ID", type="password", value=Config.GOOGLE_CX)

st.sidebar.markdown("---")
st.sidebar.subheader("Crawler Constraints")
Config.MAX_DEPTH = st.sidebar.slider("Max Crawl Depth", min_value=1, max_value=3, value=Config.MAX_DEPTH)
Config.MAX_PAGES = st.sidebar.slider("Max Pages to Scrape", min_value=3, max_value=30, value=Config.MAX_PAGES)
Config.TIMEOUT = st.sidebar.slider("Request Timeout (s)", min_value=5, max_value=30, value=Config.TIMEOUT)

# Tabs
tab_single, tab_bulk = st.tabs(["🔍 Single Lookup", "📁 Bulk Import (CSV)"])

# -----------------
# TAB 1: Single Lookup
# -----------------
with tab_single:
    st.subheader("Search Agency")
    
    col_input1, col_input2 = st.columns(2)
    with col_input1:
        search_name = st.text_input("Agency Name", placeholder="e.g. Cantiere Creativo")
    with col_input2:
        search_vat = st.text_input("VAT Number (Partita IVA) - Optional", placeholder="e.g. 01657380509")
        
    search_clicked = st.button("Generate Intelligence Report", type="primary")
    
    if search_clicked:
        if not search_name and not search_vat:
            st.warning("Please provide at least an Agency Name or a VAT number.")
        else:
            status_placeholder = st.empty()
            
            # Stepwise status display
            def update_status(msg):
                status_placeholder.info(f"🔄 {msg}")
            
            # Run pipeline
            time_start = time.time()
            try:
                # Fetch data
                results = lookup_agency(name=search_name, vat=search_vat, progress_cb=update_status)
                
                if "error" in results:
                    st.error(results["error"])
                else:
                    status_placeholder.success(f"✅ Intelligence report ready! (GATHERED IN {time.time() - time_start:.1f}s)")

                    if _last_search_error:
                        st.warning(f"⚠️ Search issue: {_last_search_error}")

                    # Display metrics/summary
                    col1, col2, col3 = st.columns(3)
                    
                    # Col 1: Registry Data
                    with col1:
                        with st.container():
                            st.subheader("🏢 Registry Data")
                            st.markdown(f"**Search Query:** `{results['search_name'] or 'Not provided'}`")
                            st.markdown(f"**Official Name:** {results['official_name'] or '*Not verified*'}")
                            
                            vat_str = results['vat_number']
                            if vat_str:
                                if results['vies_valid']:
                                    st.markdown(f"**VAT:** `{vat_str}` <span class='badge-vies-valid'>VIES VALID</span>", unsafe_allow_html=True)
                                else:
                                    st.markdown(f"**VAT:** `{vat_str}` <span class='badge-vies-invalid'>VIES INVALID/UNCHECKED</span>", unsafe_allow_html=True)
                            else:
                                st.markdown("**VAT:** *Not found / Not provided*")
                                
                            st.markdown(f"**Official Address:** {results['official_address'] or '*Not verified*'}")
                    
                    # Col 2: Digital Channels
                    with col2:
                        with st.container():
                            st.subheader("🌐 Digital Channels")
                            if results['website']:
                                st.markdown(f"**Website:** [{results['website']}]({results['website']})")
                            else:
                                st.markdown("**Website:** *Not resolved*")
                                
                            st.markdown("**Public Telephones:**")
                            if results['telephones']:
                                for t in results['telephones']:
                                    st.markdown(f"- `{t}`")
                            else:
                                st.markdown("*None extracted*")
                                
                            st.markdown("**Public Emails:**")
                            if results['emails']:
                                for e in results['emails']:
                                    st.markdown(f"- `{e}`")
                            else:
                                st.markdown("*None extracted*")
                    
                    # Col 3: Size & LinkedIn
                    with col3:
                        with st.container():
                            st.subheader("👥 Size & Contacts")
                            st.markdown(f"**Approx. Size (LinkedIn):** {results['size_estimate']}")
                            if results.get('linkedin_company_url'):
                                li_url = results['linkedin_company_url']
                                li_name = li_url.rstrip("/").split("/")[-1].replace("-", " ").title()
                                st.markdown(f"**LinkedIn Page:** [{li_name}]({li_url})")
                            st.markdown("**Key LinkedIn Contacts:**")
                            if results['linkedin_contacts']:
                                for c in results['linkedin_contacts'][:8]:
                                    src = c.get('source', '')
                                    src_label = ' [anchored]' if src == 'company_page' else f' [{src}]' if src else ''
                                    st.markdown(f"- [{c['name']}]({c['url']}) - *{c['role']}*{src_label}")
                                anchored_n = sum(1 for c in results['linkedin_contacts'] if c.get('source') == 'company_page')
                                other_n = len(results['linkedin_contacts']) - anchored_n
                                if other_n > 0:
                                    st.caption(f"Source: company-page anchored ({anchored_n}) + secondary sweep ({other_n})")
                                else:
                                    st.caption("Source: anchored to LinkedIn company page")
                            else:
                                if results.get('linkedin_company_url'):
                                    st.markdown("*No LinkedIn profiles matched the company page*")
                                else:
                                    st.markdown("*No LinkedIn company page was resolved — contacts skipped*")
                            st.info("LinkedIn restricts indexing; results may be partial. Verify before outreach.")
                                
                    # Row 2: Services & Payments
                    col4, col5 = st.columns(2)
                    
                    with col4:
                        with st.container():
                            st.subheader("🛠️ Services Extracted")
                            if results['services']:
                                for s in results['services']:
                                    st.markdown(f"- {s}")
                            else:
                                st.markdown("*Could not extract service listings*")
                                
                    with col5:
                        with st.container():
                            st.subheader("💳 Payment Gateway Integration")
                            pay_info = results['payment_integration']
                            if pay_info['provides_payment_integration']:
                                st.markdown("<span class='badge-payment-yes'>CONFIRMED: Supports payment integration</span>", unsafe_allow_html=True)
                                
                                st.markdown("**Supported Providers:**")
                                if pay_info['payment_providers']:
                                    for p in pay_info['payment_providers']:
                                        st.markdown(f"<span class='provider-tag'>{p}</span>", unsafe_allow_html=True)
                                else:
                                    st.markdown("*Generic integrations (Stripe/PayPal/Credit Cards)*")
                                    
                                st.markdown("**Associated Platforms:**")
                                if pay_info['associated_services']:
                                    for s in pay_info['associated_services']:
                                        st.markdown(f"- {s}")
                            else:
                                st.markdown("<span class='badge-payment-no'>NO EXPLICIT MENTION FOUND</span>", unsafe_allow_html=True)
                                st.write("The website does not explicitly list payment gateways (Stripe, PayPal, Nexi, etc.) or ecommerce checkout systems.")

                    # Row 3: Portfolio sites
                    with st.container():
                        st.subheader("📂 Websites Created / Worked With")
                        if results['portfolio_sites']:
                            # Put domains in a multi-column grid
                            cols = st.columns(4)
                            for i, d in enumerate(results['portfolio_sites'][:20]):
                                cols[i % 4].markdown(f"🔗 [{d}](http://{d})")
                            if len(results['portfolio_sites']) > 20:
                                st.info(f"and {len(results['portfolio_sites']) - 20} more domains extracted.")
                        else:
                            st.markdown("*No client portfolio websites extracted*")

                    # Export single report
                    st.markdown("---")
                    st.subheader("Export Report")
                    
                    # Convert to MD
                    md_report = f"""# Intelligence Report: {results['official_name'] or search_name}
- **Website:** {results['website']}
- **VAT Number:** {results['vat_number']} (VIES Valid: {results['vies_valid']})
- **Registered Address:** {results['official_address']}
- **Estimated Size:** {results['size_estimate']}
- **Public Phones:** {", ".join(results['telephones'])}
- **Public Emails:** {", ".join(results['emails'])}

## Services Provided
{chr(10).join(['- ' + s for s in results['services']])}

## Payment Integration Confirmation
- **Confirmed:** {pay_info['provides_payment_integration']}
- **Providers:** {", ".join(pay_info['payment_providers'])}
- **Platforms:** {", ".join(pay_info['associated_services'])}

## Key LinkedIn Contacts
{chr(10).join([f"- {c['name']} ({c['role']}) - {c['url']}" for c in results['linkedin_contacts']])}

## Portfolios/Websites Created
{chr(10).join(['- ' + d for d in results['portfolio_sites']])}
"""
                    col_dl1, col_dl2 = st.columns(2)
                    with col_dl1:
                        st.download_button(
                            "Download Markdown Report",
                            data=md_report,
                            file_name=f"agency_{results['vat_number'] or 'report'}.md",
                            mime="text/markdown"
                        )
                    with col_dl2:
                        st.download_button(
                            "Download JSON Data",
                            data=json.dumps(results, indent=2),
                            file_name=f"agency_{results['vat_number'] or 'report'}.json",
                            mime="application/json"
                        )
            except Exception as e:
                status_placeholder.error(f"Failed to gather intelligence: {str(e)}")
                st.exception(e)

# -----------------
# TAB 2: Bulk Import (CSV)
# -----------------
with tab_bulk:
    st.subheader("Bulk Import from CSV")
    st.write("Upload a CSV file containing the list of agencies. The CSV should contain columns named **name** and/or **vat**.")
    
    # Template instruction
    st.markdown("""
    **Required CSV Format Example:**
    ```csv
    name,vat
    Cantiere Creativo,
    ,01657380509
    Belka,
    ```
    """)
    
    uploaded_file = st.file_uploader("Choose a CSV file", type=["csv"])
    
    if uploaded_file is not None:
        df = pd.read_csv(uploaded_file)
        st.write("Preview of Uploaded Data:")
        st.dataframe(df.head(10))
        
        # Check columns
        cols = [c.lower().strip() for c in df.columns]
        df.columns = cols
        
        if "name" not in cols and "vat" not in cols:
            st.error("CSV file must contain at least a 'name' or 'vat' column.")
        else:
            run_bulk = st.button("Run Bulk Intelligence Extraction", type="primary")
            
            if run_bulk:
                progress_bar = st.progress(0)
                status_text = st.empty()
                
                bulk_results = []
                total_rows = len(df)
                
                time_bulk_start = time.time()
                for idx, row in df.iterrows():
                    name_val = str(row["name"]).strip() if "name" in cols and pd.notna(row["name"]) else None
                    vat_val = str(row["vat"]).strip() if "vat" in cols and pd.notna(row["vat"]) else None
                    
                    def update_bulk_progress(msg):
                        status_text.info(f"⚙️ Processing row {idx + 1}/{total_rows} (**{name_val or vat_val}**): {msg}")
                    
                    try:
                        res = lookup_agency(name=name_val, vat=vat_val, progress_cb=update_bulk_progress)
                        bulk_results.append(res)
                    except Exception as e:
                        bulk_results.append({
                            "search_name": name_val,
                            "search_vat": vat_val,
                            "error": str(e)
                        })
                    
                    # Update progress
                    progress_bar.progress((idx + 1) / total_rows)
                    time.sleep(0.5)  # Polite delay
                    
                status_text.success(f"✅ Processed {total_rows} agencies in {time.time() - time_bulk_start:.1f} seconds!")
                
                # Format bulk results for tabular presentation
                flat_results = []
                for res in bulk_results:
                    if "error" in res:
                        flat_results.append({
                            "Search Name": res.get("search_name", ""),
                            "Search VAT": res.get("search_vat", ""),
                            "Official Name": "ERROR",
                            "VAT Number": "ERROR",
                            "VIES Status": "ERROR",
                            "Website": "",
                            "Phones": "",
                            "Emails": "",
                            "Size": "",
                            "Payment Integration": "No",
                            "Payment Providers": "",
                            "Services Count": 0,
                            "Portfolio Count": 0
                        })
                    else:
                        pay_info = res["payment_integration"]
                        flat_results.append({
                            "Search Name": res.get("search_name", ""),
                            "Search VAT": res.get("search_vat", ""),
                            "Official Name": res.get("official_name", ""),
                            "VAT Number": res.get("vat_number", ""),
                            "VIES Status": "VALID" if res.get("vies_valid") else "INVALID/UNKNOWN",
                            "Website": res.get("website", ""),
                            "Phones": ", ".join(res.get("telephones", [])),
                            "Emails": ", ".join(res.get("emails", [])),
                            "Size": res.get("size_estimate", ""),
                            "Payment Integration": "Yes" if pay_info.get("provides_payment_integration") else "No",
                            "Payment Providers": ", ".join(pay_info.get("payment_providers", [])),
                            "Services Count": len(res.get("services", [])),
                            "Portfolio Count": len(res.get("portfolio_sites", []))
                        })
                
                results_df = pd.DataFrame(flat_results)
                st.subheader("Extraction Summary")
                st.dataframe(results_df)
                
                # Provide downloads
                col_dl_bulk1, col_dl_bulk2 = st.columns(2)
                
                # CSV Export
                csv_buffer = io.StringIO()
                results_df.to_csv(csv_buffer, index=False)
                csv_data = csv_buffer.getvalue()
                
                with col_dl_bulk1:
                    st.download_button(
                        "Download Consolidated CSV",
                        data=csv_data,
                        file_name="bulk_agency_report.csv",
                        mime="text/csv"
                    )
                    
                # JSON Export
                with col_dl_bulk2:
                    st.download_button(
                        "Download Raw JSON Data",
                        data=json.dumps(bulk_results, indent=2),
                        file_name="bulk_agency_report.json",
                        mime="application/json"
                    )
