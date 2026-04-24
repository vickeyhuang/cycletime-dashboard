import streamlit as st
import pandas as pd
import tempfile
from datetime import datetime

# Import all functions of original code file
from delivery_dashboard_generator import (
    classify_team, 
    prepare_metrics, 
    aggregate_metrics_for_dashboard,
    generate_html
)

def main():
    st.set_page_config(page_title="Delivery Dashboard Generator", layout="wide")
    
    st.title("📊 Delivery Performance Dashboard Generator")
    st.markdown("Upload Jira CSV file, it will auto generate a delivery dashboard.")
    
    uploaded_file = st.file_uploader("Please choose Jira CSV file", type=['csv'])
    xian_file = st.file_uploader("Please choose Xian engineers list (CSV)", type=['csv'])
    
    if uploaded_file and xian_file:
        try:
            jira_df = pd.read_csv(uploaded_file)
            xian_engineers_df = pd.read_csv(xian_file)
            
            engineers = set(xian_engineers_df['Engineer'].str.strip().str.lower())
            jira_df = classify_team(jira_df, engineers)
            jira_df = prepare_metrics(jira_df)
            
            available_quarters = sorted(jira_df['Year-Quarter'].unique())
            summary, _ = aggregate_metrics_for_dashboard(jira_df)
            
            html_content = generate_html(summary, available_quarters, jira_df)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            st.success("✅ Dashboard generated successfully！")
            
            st.download_button(
                label="📥 Download the Dashboard",
                data=html_content,
                file_name=f"delivery_dashboard_{timestamp}.html",
                mime="text/html"
            )
            
            st.subheader("📈 Dashboard Preview")
            st.components.v1.html(html_content, height=800, scrolling=True)
            
        except Exception as e:
            st.error(f"❌ An error occurred while processing the data：{str(e)}")

if __name__ == "__main__":
    main()