import pandas as pd
import os
import glob
import json
from functools import partial

# ===== Config =====
JIRA_OUTPUT_FOLDER = "output_files"
XIAN_ENGINEERS_FILE = "xian_engineers.csv"

OUTPUT_FOLDER = "dashboard"
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

from datetime import datetime    
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

# Generate dashboard html with timestamp (history version)
OUTPUT_HTML_TIMESTAMP = os.path.join(OUTPUT_FOLDER, f"delivery_dashboard_{timestamp}.html")

# File without timestamp (latest version)
OUTPUT_HTML_LATEST = os.path.join( OUTPUT_FOLDER, "delivery_dashboard.html")

# ===== Load latest Jira CSV =====
def load_latest_jira_csv():
    files = glob.glob(os.path.join(JIRA_OUTPUT_FOLDER, "*.csv"))
    if not files:
        raise Exception("No CSV files found in output_files")
    latest = max(files, key=os.path.getmtime)
    print("Loading:", latest)
    df = pd.read_csv(latest)
    return df

# ===== Load Xian engineers =====
def load_xian_engineers():
    df = pd.read_csv(XIAN_ENGINEERS_FILE)
    engineers = set(df['Engineer'].str.strip().str.lower())
    return engineers

# ===== Classify team =====
def classify_team(df, xian_engineers):
    df['Assignee_clean'] = df['Assignee'].astype(str).str.strip().str.lower()
    df['Team'] = df['Assignee_clean'].apply(lambda x: 'Xian' if x in xian_engineers else 'Sydney')
    return df

# ===== Prepare metrics =====
def prepare_metrics(df):
    df = df.copy()
    df['Story Points'] = pd.to_numeric(df['Story Points'], errors='coerce').fillna(0)
    df['Converted Cycle Time (d)'] = pd.to_numeric(df['Converted Cycle Time (d)'], errors='coerce')

    # Deal with Block Time - handle two block name
    if 'Blocked (d)' in df.columns:
        df['Block Time (d)'] = pd.to_numeric(df['Blocked (d)'], errors='coerce').fillna(0)
    elif 'Blocked Time (d)' in df.columns:
        df['Block Time (d)'] = pd.to_numeric(df['Blocked Time (d)'], errors='coerce').fillna(0)
    else:
        print("Warning: No Block Time column found. Setting Block Time to 0.")
        df['Block Time (d)'] = 0

    # Print Block Time info after processed
    print(f"Block Time column exists: {'Block Time (d)' in df.columns}")
    print(f"Block Time sum: {df['Block Time (d)'].sum()}")
    print(f"Block Time non-zero count: {(df['Block Time (d)'] > 0).sum()}")    

    # Save original date format
    original_dates = df['Done in Month'].copy()
    
    # Support multiple date format
    formats = ['%b-%y', '%Y-%m', '%b %Y', '%Y-%m-%d']
    
    # Init result is None
    df['Done in Month'] = pd.NaT
    
    # Try each method
    for fmt in formats:
        mask = df['Done in Month'].isna()
        if mask.any():
            df.loc[mask, 'Done in Month'] = pd.to_datetime(
                original_dates[mask], format=fmt, errors='coerce'
            )
    
    # Try process via default parser
    mask = df['Done in Month'].isna()
    if mask.any():
        df.loc[mask, 'Done in Month'] = pd.to_datetime(
            original_dates[mask], errors='coerce'
        )
    
    df = df[df['Done in Month'].notna()]
    df['Year-Quarter'] = df['Done in Month'].dt.to_period('Q').astype(str)
    df['Year-Month'] = df['Done in Month'].dt.strftime('%Y-%m')
    df['Month'] = df['Done in Month'].dt.strftime('%b %Y')
    return df

# ===== Aggregate metrics for dashboard with quarter filter =====
def aggregate_metrics_for_dashboard(df, selected_quarters=None):
    # Filter by selected quarters if provided
    if selected_quarters and len(selected_quarters) > 0:
        df = df[df['Year-Quarter'].isin(selected_quarters)]
        print(f"Filtering for quarters: {selected_quarters}")
    
    print(f"Data after filtering: {len(df)} rows")
    
    # Get available quarters for filter
    available_quarters = sorted(df['Year-Quarter'].unique())
    
    if len(df) == 0:
        print("Warning: No data after filtering")
        return pd.DataFrame(), available_quarters

    # Aggregate by Project and Team (Sydney and Xian only)
    project_team_summary = df.groupby(['Project', 'Team']).agg(
        Issues=('Issue Key', 'count'),
        StoryPoints=('Story Points', 'sum'),
        TotalCycleDays=('Converted Cycle Time (d)', 'sum'),
        TotalBlockTime=('Block Time (d)', 'sum')
    ).reset_index()
    
    # Calculate average cycle time for each team
    project_team_summary['AvgCycleTime'] = (project_team_summary['TotalCycleDays'] / project_team_summary['Issues']).round(1)
    
    # 计算 AvgBlockTime - 放在正确的位置
    project_team_summary['AvgBlockTime'] = project_team_summary.apply(
        lambda row: row['TotalBlockTime'] / row['Issues'] if row['Issues'] > 0 else 0,
        axis=1
    ).round(1)

    # Calculate Overall per project (sum of both teams)
    project_overall = df.groupby(['Project']).agg(
        Issues=('Issue Key', 'count'),
        StoryPoints=('Story Points', 'sum'),
        TotalCycleDays=('Converted Cycle Time (d)', 'sum'),
        TotalBlockTime=('Block Time (d)', 'sum')
    ).reset_index()
    
    project_overall['AvgCycleTime'] = (project_overall['TotalCycleDays'] / project_overall['Issues']).round(1)
    
    # 计算 AvgBlockTime - 放在 project_overall 创建之后
    project_overall['AvgBlockTime'] = project_overall.apply(
        lambda row: row['TotalBlockTime'] / row['Issues'] if row['Issues'] > 0 else 0,
        axis=1
    ).round(1)

    project_overall['Team'] = 'Overall'
    
    # Combine all data
    all_data = pd.concat([project_overall, project_team_summary], ignore_index=True)
    
    # Calculate Xian Faster % compared to Overall
    xian_faster_pct = []
    for project in all_data['Project'].unique():
        overall_row = all_data[(all_data['Project'] == project) & (all_data['Team'] == 'Overall')]
        xian_row = all_data[(all_data['Project'] == project) & (all_data['Team'] == 'Xian')]
        
        if len(overall_row) > 0 and len(xian_row) > 0:
            overall_time = overall_row['AvgCycleTime'].values[0]
            xian_time = xian_row['AvgCycleTime'].values[0]
            if overall_time > 0:
                pct = ((overall_time - xian_time) / overall_time * 100).round(1)
            else:
                pct = 0
        else:
            pct = 0
        
        xian_faster_pct.append({'Project': project, 'XianFasterPct': pct})
    
    pct_df = pd.DataFrame(xian_faster_pct)
    
    # Calculate Xian completion percentages per project
    completion_data = []
    for project in project_overall['Project'].unique():
        xian_row = project_team_summary[(project_team_summary['Project'] == project) & (project_team_summary['Team'] == 'Xian')]
        sydney_row = project_team_summary[(project_team_summary['Project'] == project) & (project_team_summary['Team'] == 'Sydney')]
        
        xian_issues = xian_row['Issues'].values[0] if len(xian_row) > 0 else 0
        xian_points = xian_row['StoryPoints'].values[0] if len(xian_row) > 0 else 0
        sydney_issues = sydney_row['Issues'].values[0] if len(sydney_row) > 0 else 0
        sydney_points = sydney_row['StoryPoints'].values[0] if len(sydney_row) > 0 else 0
        
        total_issues = xian_issues + sydney_issues
        total_points = xian_points + sydney_points
        
        xian_issues_pct = round((xian_issues / total_issues * 100), 1) if total_issues > 0 else 0
        xian_points_pct = round((xian_points / total_points * 100), 1) if total_points > 0 else 0
        
        completion_data.append({
            'Project': project,
            'XianIssuesPct': xian_issues_pct,
            'XianPointsPct': xian_points_pct
        })
    
    completion_df = pd.DataFrame(completion_data)
    
    # Merge all data together
    final_data = all_data.merge(pct_df, on='Project', how='left')
    final_data = final_data.merge(completion_df, on='Project', how='left')
    
    print(f"Final summary has {len(final_data)} rows")
    print("=== Block Time Summary ===")
    print(final_data[['Project', 'Team', 'TotalBlockTime', 'TotalCycleDays', 'AvgCycleTime', 'TotalBlockTime','AvgBlockTime']].to_string())

    return final_data, available_quarters

# ===== Generate HTML Dashboard =====
def generate_html(df, available_quarters, jira_df):
    # Store the raw data for JavaScript filtering
    # We need to store the raw quarterly data to enable client-side filtering
    
    # Prepare quarterly data for all projects and teams
    quarterly_data = []
    for quarter in available_quarters:
        quarter_df = jira_df[jira_df['Year-Quarter'] == quarter]
        if len(quarter_df) > 0:
            # Get unique months in this quarter
            months_in_quarter = sorted(quarter_df['Year-Month'].unique())

            # Aggregate by project and team for this quarter
            quarter_summary = quarter_df.groupby(['Project', 'Team']).agg(
                Issues=('Issue Key', 'count'),
                StoryPoints=('Story Points', 'sum'),
                TotalCycleDays=('Converted Cycle Time (d)', 'sum'),
                TotalBlockTime=('Block Time (d)', 'sum')  # 添加这一行
            ).reset_index()
            
            # Calculate avg cycle time
            quarter_summary['AvgCycleTime'] = (quarter_summary['TotalCycleDays'] / quarter_summary['Issues']).round(1)

            # Add AvgBlockTime calculation
            quarter_summary['AvgBlockTime'] = quarter_summary.apply(
                lambda row: row['TotalBlockTime'] / row['Issues'] if row['Issues'] > 0 else 0,
                axis=1
            ).round(1)

            for _, row in quarter_summary.iterrows():
                quarterly_data.append({
                    'Quarter': quarter,
                    'Project': row['Project'],
                    'Team': row['Team'],
                    'Issues': int(row['Issues']),
                    'StoryPoints': int(row['StoryPoints']),
                    'TotalCycleDays': round(row['TotalCycleDays'], 1),
                    'TotalBlockTime': round(row['TotalBlockTime'], 1),  # 添加这一行
                    'AvgCycleTime': round(row['AvgCycleTime'], 1),
                    'AvgBlockTime': round(row['AvgBlockTime'], 1),  # 添加这一行
                    'Months': months_in_quarter
                })
            
            print("=== Quarter Summary Block Time ===")
            print(quarter_summary[['Project', 'Team', 'TotalBlockTime', 'TotalCycleDays', 'TotalBlockTime', 'AvgBlockTime']].head(10))

            # Add Overall for this quarter
            quarter_overall = quarter_df.groupby(['Project']).agg(
                Issues=('Issue Key', 'count'),
                StoryPoints=('Story Points', 'sum'),
                TotalCycleDays=('Converted Cycle Time (d)', 'sum'),
                TotalBlockTime=('Block Time (d)', 'sum')  # 添加这一行
            ).reset_index()
            quarter_overall['AvgCycleTime'] = (quarter_overall['TotalCycleDays'] / quarter_overall['Issues']).round(1)

            # Add AvgBlockTime calculation
            quarter_overall['AvgBlockTime'] = quarter_overall.apply(
                lambda row: row['TotalBlockTime'] / row['Issues'] if row['Issues'] > 0 else 0,
                axis=1
            ).round(1)

            for _, row in quarter_overall.iterrows():
                quarterly_data.append({
                    'Quarter': quarter,
                    'Project': row['Project'],
                    'Team': 'Overall',
                    'Issues': int(row['Issues']),
                    'StoryPoints': int(row['StoryPoints']),
                    'TotalCycleDays': round(row['TotalCycleDays'], 1),
                    'TotalBlockTime': round(row['TotalBlockTime'], 1),
                    'AvgCycleTime': round(row['AvgCycleTime'], 1),
                    'AvgBlockTime': round(row['AvgBlockTime'], 1),  # 添加这一行
                    'Months': months_in_quarter
                })
    
    # 在 quarterly_data 生成后，添加调试打印
    print("=== Quarterly Data Sample (first 2 items) ===")
    for item in quarterly_data[:2]:
        print(f"Project: {item['Project']}, Team: {item['Team']}, TotalBlockTime: {item.get('TotalBlockTime', 'MISSING')}")

    # Prepare monthly data for Xian team
    monthly_data = []
    jira_df_xian = jira_df[jira_df['Team'] == 'Xian']
    
    for month in sorted(jira_df_xian['Year-Month'].unique()):
        month_df = jira_df_xian[jira_df_xian['Year-Month'] == month]
        month_label = month_df['Month'].iloc[0] if len(month_df) > 0 and 'Month' in month_df.columns else month
        
        monthly_issues = len(month_df)
        monthly_points = month_df['Story Points'].sum()
        
        monthly_data.append({
            'Month': month,
            'MonthLabel': month_label,
            'Issues': int(monthly_issues),
            'StoryPoints': int(monthly_points)
        })

    print(f"Monthly data count: {len(monthly_data)}")
    for item in monthly_data:
        print(f"  {item['MonthLabel']}: Issues={item['Issues']}, Points={item['StoryPoints']}")
    
    quarterly_json = json.dumps(quarterly_data)

    monthly_json = json.dumps(monthly_data)
    quarters_json = json.dumps(available_quarters)

    # Generate quarter buttons HTML
    quarter_buttons_html = ''
    for quarter in available_quarters:
        quarter_buttons_html += f'<button class="quarter-btn" onclick="toggleQuarter(\'{quarter}\')">{quarter}</button>\n'

    html_content = f'''<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Delivery Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@3.9.1"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-datalabels@2.0.0"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-annotation@2.0.0"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js"></script>
<script>
    // Register
    Chart.register(ChartDataLabels);
    Chart.register(ChartAnnotation);
</script>
<style>
* {{
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}}

body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
    background: #EDF1F3;
    padding: 30px;
}}

h1 {{
    text-align: center;
    color: #1a2639;
    margin-bottom: 30px;
    font-size: 2.5em;
    font-weight: 600;
}}

.controls {{
    background: white;
    padding: 25px;
    border-radius: 16px;
    box-shadow: 0 4px 20px rgba(0,0,0,0.08);
    margin-bottom: 30px;
    border: 1px solid rgba(0,0,0,0.05);
}}

.filter-header {{
    display: flex;
    align-items: center;
    margin-bottom: 20px;
    padding-bottom: 15px;
    border-bottom: 2px solid #f0f2f5;
}}

.filter-header h3 {{
    color: #1a2639;
    font-size: 1.2em;
    font-weight: 600;
    display: flex;
    align-items: center;
    gap: 8px;
}}

.filter-header h3:before {{
    content: '📅';
    font-size: 1.4em;
}}

.quarter-buttons {{
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    margin-bottom: 25px;
    padding: 10px 0;
}}

.quarter-btn {{
    padding: 12px 24px;
    border: 2px solid #e1e5e9;
    border-radius: 40px;
    background: white;
    color: #4a5568;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.2s ease;
    box-shadow: 0 2px 4px rgba(0,0,0,0.02);
}}

.quarter-btn:hover {{
    border-color: #2d9cdb;
    background: #f0f9ff;
    color: #2d9cdb;
    transform: translateY(-1px);
    box-shadow: 0 4px 8px rgba(45,156,219,0.15);
}}

.quarter-btn.selected {{
    background: #2d9cdb;
    border-color: #2d9cdb;
    color: white;
    box-shadow: 0 4px 12px rgba(45,156,219,0.3);
}}

.action-buttons {{
    display: flex;
    gap: 15px;
    margin: 25px 0 20px;
    padding-top: 15px;
    border-top: 2px dashed #f0f2f5;
}}

.action-btn {{
    padding: 12px 30px;
    border: none;
    border-radius: 40px;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.2s ease;
    display: inline-flex;
    align-items: center;
    gap: 8px;
}}

.apply-btn {{
    background: #2d9cdb;
    color: white;
    box-shadow: 0 4px 12px rgba(45,156,219,0.3);
}}

.apply-btn:hover {{
    background: #2483b5;
    transform: translateY(-2px);
    box-shadow: 0 6px 16px rgba(45,156,219,0.4);
}}

.clear-btn {{
    background: #f1f3f5;
    color: #4a5568;
}}

.clear-btn:hover {{
    background: #e5e9ed;
    transform: translateY(-2px);
}}

.select-all-btn {{
    background: #e8f4fe;
    color: #2d9cdb;
}}

.select-all-btn:hover {{
    background: #d1e9fd;
    transform: translateY(-2px);
}}

.active-filters {{
    background: #f8fafc;
    padding: 16px 20px;
    border-radius: 40px;
    font-size: 14px;
    color: #4a5568;
    border: 1px solid #e1e5e9;
    display: flex;
    align-items: center;
    gap: 10px;
}}

.active-filters strong {{
    color: #1a2639;
    font-weight: 600;
}}

#selectedQuarters {{
    color: #2d9cdb;
    font-weight: 500;
}}

.summary-stats {{
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: 20px;
    margin: 30px 0;
}}

.stat-box {{
    background: white;
    padding: 20px;
    border-radius: 16px;
    box-shadow: 0 4px 20px rgba(0,0,0,0.08);
    text-align: center;
    transition: transform 0.2s ease;
    border: 1px solid rgba(0,0,0,0.05);
    position: relative;
}}

.stat-box:hover {{
    transform: translateY(-2px);
    box-shadow: 0 8px 24px rgba(0,0,0,0.12);
}}

.stat-value {{
    font-size: 36px;
    font-weight: 700;
    color: #2d9cdb;
    margin-bottom: 5px;
}}

.stat-label {{
    font-size: 14px;
    color: #718096;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}}

.stat-sub {{
    position: absolute;
    top: 10px;
    right: 15px;
    font-size: 14px;
    font-weight: 600;
    color: #6B9E78;
    background: #E8F8F0;
    padding: 4px 10px;
    border-radius: 20px;
    display: flex;
    align-items: center;
    gap: 4px;
}}

.stat-sub::before {{
    font-size: 12px;
    font-weight: normal;
    color: #2c3e50;
}}

.chart-container {{
    background: white;
    padding: 25px;
    border-radius: 16px;
    box-shadow: 0 4px 20px rgba(0,0,0,0.08);
    margin-top: 30px;
    border: 1px solid rgba(0,0,0,0.05);
    position: relative;
    width: 100%;
}}

.chart-container h2 {{
    color: #1a2639;
    font-size: 1.3em;
    margin-bottom: 20px;
    padding-bottom: 15px;
    border-bottom: 2px solid #f0f2f5;
    display: flex;
    align-items: center;
    gap: 8px;
}}

canvas {{
    max-height: 400px;
    width: 100% !important;
    height: auto !important;
}}

.monthly-charts-section {{
    margin-top: 30px;
}}

.section-title {{
    color: #1a2639;
    font-size: 1.5em;
    margin-bottom: 20px;
    padding-bottom: 10px;
    border-bottom: 2px solid #e1e5e9;
    display: flex;
    align-items: center;
    gap: 10px;
}}

.monthly-charts-container {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 20px;
}}

.monthly-chart {{
    margin-top: 0 !important;
}}

.legend-item {{
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 13px;
    color: #2c3e50;
    font-weight: 500;
}}

.legend-color {{
    width: 20px;
    height: 20px;
    border-radius: 6px;
}}

.legend-title {{
    font-weight: 600;
    color: #1a2639;
    margin-right: 5px;
}}

.data-table {{
    background: white;
    padding: 25px;
    border-radius: 16px;
    box-shadow: 0 4px 20px rgba(0,0,0,0.08);
    margin-top: 30px;
    border: 1px solid rgba(0,0,0,0.05);
    overflow-x: auto;
}}

.data-table h2 {{
    color: #1a2639;
    font-size: 1.3em;
    margin-bottom: 20px;
    padding-bottom: 15px;
    border-bottom: 2px solid #f0f2f5;
    display: flex;
    align-items: center;
    gap: 8px;
}}

table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 14px;
}}

th {{
    background: #f8fafc;
    color: #1a2639;
    font-weight: 600;
    padding: 10px;
    text-align: left;
    cursor: pointer;
    transition: background 0.2s ease;
    border-bottom: 2px solid #e1e5e9;
}}

th:hover {{
    background: #edf2f7;
}}

td {{
    padding: 15px;
    border-bottom: 1px solid #f0f2f5;
    color: #4a5568;
}}

tr:hover td {{
    background: #f8fafc;
}}

.positive {{
    color: #27ae60;
    font-weight: 700;
}}

.negative {{
    color: #e74c3c;
    font-weight: 700;
}}

.overall-row {{
    background-color: #f3e8ff;
    font-weight: 500;
}}

.total-row {{
    background-color: #2d3e50;
    color: white;
    font-weight: 700;
    border-top: 3px solid #1a2639;
}}

.total-row td {{
    color: white;
    font-weight: 700;
}}

/* Override hover for total row */
.total-row:hover td {{
    background-color: #e8f4fe; 
    color: #2d9cdb;
}}

.download-btn {{
    background: #27ae60 !important;
    color: white !important;
    margin-left: auto;
}}

.download-btn:hover {{
    background: #219a52 !important;
    transform: translateY(-2px);
    box-shadow: 0 6px 16px rgba(39, 174, 96, 0.4);
}}

.download-btn:disabled {{
    opacity: 0.6;
    cursor: not-allowed;
    transform: none;
    box-shadow: none;
}}

@media (max-width: 768px) {{
    body {{ padding: 15px; }}
    .quarter-buttons {{ gap: 8px; }}
    .quarter-btn {{ padding: 8px 16px; font-size: 12px; }}
    .action-buttons {{ flex-direction: column; }}
    .action-btn {{ width: 100%; justify-content: center; }}
    .summary-stats {{ grid-template-columns: 1fr; }}
    .monthly-charts-container {{ grid-template-columns: 1fr; }}
}}
</style>
</head>
<body>

<h1>📊 Delivery Performance Dashboard</h1>

<div class="controls">
    <div class="filter-header">
        <h3>Filter by Quarter</h3>
    </div>
    
    <div class="quarter-buttons" id="quarterButtons">
        {quarter_buttons_html}
    </div>
    
    <div class="action-buttons">
        <button class="action-btn apply-btn" onclick="applyFilter()">
            <span>✓</span> Apply Filter
        </button>
        <button class="action-btn clear-btn" onclick="clearFilter()">
            <span>↺</span> Clear Filter
        </button>
        <button class="action-btn select-all-btn" onclick="selectAllQuarters()">
            <span>⊕</span> Select All
        </button>
    </div>
    
    <div class="active-filters">
        <strong>📌 Selected Quarters:</strong>
        <span id="selectedQuarters">All Quarters</span>
    </div>
</div>

<div class="summary-stats" id="summaryStats">
    <div class="stat-box">
        <div class="stat-value" id="totalProjects">0</div>
        <div class="stat-label">TOTAL PROJECTS</div>
    </div>
    <div class="stat-box">
        <div class="stat-value" id="totalIssues">0</div>
        <div class="stat-label">TOTAL ISSUES COMPLETION</div>
        <div class="stat-sub" id="xianIssuesPct">0%</div>
    </div>
    <div class="stat-box">
        <div class="stat-value" id="totalPoints">0</div>
        <div class="stat-label">TOTAL STORY POINTS COMPLETION</div>
        <div class="stat-sub" id="xianPointsPct">0%</div>
    </div>
    <div class="stat-box">
        <div class="stat-value" id="avgCycleTime">0.0</div>
        <div class="stat-label">AVG CYCLE TIME (DAYS)</div>
        <div class="stat-sub" id="xianFasterPct">0%</div>
    </div>
    <div class="stat-box">
        <div class="stat-value" id="avgBlockTime">0.0</div>
        <div class="stat-label">AVG BLOCK TIME (DAYS)</div>
        <div class="stat-sub" id="xianLessBlockedPct">0%</div>
    </div>
</div>

<div class="chart-container">
    <h2><span>📈</span> Issues Completed by Project</h2>
    <canvas id="issuesChart"></canvas>
</div>

<div class="chart-container">
    <h2><span>📊</span> Story Points Delivered by Project</h2>
    <canvas id="pointsChart"></canvas>
</div>

<div class="chart-container">
    <h2><span>⏱️</span> Cycle Time & Xian Faster % (vs Overall)</h2>
    <canvas id="cycleChart"></canvas>
</div>

<div class="chart-container">
    <h2><span>⏱️</span> Avg Block Time & Xian Less Blocked % (vs Overall)</h2>
    <canvas id="avgBlockTimeChart"></canvas>
</div>

<div class="monthly-charts-section">
    <div class="monthly-charts-container">
        <div class="chart-container monthly-chart">
            <h2><span>📈</span> Monthly Completed Issues by Xian</h2>
            <canvas id="monthlyIssuesChart"></canvas>
        </div>
        <div class="chart-container monthly-chart">
            <h2><span>📊</span> Monthly Completed Story Points by Xian</h2>
            <canvas id="monthlyPointsChart"></canvas>
        </div>
    </div>
</div>

<div class="data-table">
    <h2><span>📋</span> Detailed Data</h2>
    <table id="dataTable">
        <thead>
            <tr>
                <th onclick="sortTable(0)">Project ⬍</th>
                <th onclick="sortTable(1)">Team ⬍</th>
                <th onclick="sortTable(2)">Issues ⬍</th>
                <th onclick="sortTable(3)">Issues %</th>
                <th onclick="sortTable(4)">Story Points ⬍</th>
                <th onclick="sortTable(5)">Story Points %</th>
                <th onclick="sortTable(6)">Total Cycle Days ⬍</th>
                <th onclick="sortTable(7)">Avg Cycle Time ⬍</th>
                <th>Xian Faster %</th>
                <th onclick="sortTable(8)">Block Time ⬍</th>
                <th onclick="sortTable(9)">Avg Block Time ⬍</th>
                <th>Xian Less Blocked %</th>
            </tr>
        </thead>
        <tbody id="tableBody">
        </tbody>
        <tfoot id="tableFooter">
        </tfoot>
    </table>
</div>

<script>
// Raw quarterly data for filtering
const quarterlyData = {quarterly_json};
const monthlyData = {monthly_json};
const allQuarters = {quarters_json};
let currentData = [];
let pointsChart, issuesChart, cycleChart, avgBlockTimeChart;
let monthlyIssuesChart, monthlyPointsChart;
let sortColumn = 0;
let sortAscending = true;
let selectedQuarters = new Set();

function toggleQuarter(quarter) {{
    const button = event.target;
    if (selectedQuarters.has(quarter)) {{
        selectedQuarters.delete(quarter);
        button.classList.remove('selected');
    }} else {{
        selectedQuarters.add(quarter);
        button.classList.add('selected');
    }}
    updateActiveFilters();
}}

function updateActiveFilters() {{
    const displayEl = document.getElementById('selectedQuarters');
    const selectedArray = Array.from(selectedQuarters);
    
    if (selectedArray.length === 0 || selectedArray.length === allQuarters.length) {{
        displayEl.innerHTML = 'All Quarters';
    }} else {{
        displayEl.innerHTML = selectedArray.sort().join(' • ');
    }}
}}

function aggregateData(selectedQuartersSet) {{
    let filteredQuarterly = quarterlyData;
    const selectedArray = Array.from(selectedQuartersSet);
    
    if (selectedArray.length > 0 && selectedArray.length < allQuarters.length) {{
        filteredQuarterly = quarterlyData.filter(d => selectedArray.includes(d.Quarter));
    }}
    
    const aggregated = {{}};
    
    filteredQuarterly.forEach(item => {{
        const key = item.Project + '|' + item.Team;
        if (!aggregated[key]) {{
            aggregated[key] = {{
                Project: item.Project,
                Team: item.Team,
                Issues: 0,
                StoryPoints: 0,
                TotalCycleDays: 0,
                TotalBlockTime: 0,
                count: 0
            }};
        }}
        aggregated[key].Issues += item.Issues;
        aggregated[key].StoryPoints += item.StoryPoints;
        aggregated[key].TotalCycleDays += item.TotalCycleDays;
        aggregated[key].TotalBlockTime += item.TotalBlockTime;
        aggregated[key].count++;
    }});
    
    const result = [];
    const projectTeams = {{}};
    const projectTotals = {{}};
    
    // First pass: calculate team data
    Object.values(aggregated).forEach(item => {{
        const avgCycleTime = item.TotalCycleDays > 0 && item.Issues > 0 
            ? item.TotalCycleDays / item.Issues 
            : 0;
        
        // Calculate Avg Block Time
        const avgBlockTime = item.Issues > 0 
            ? item.TotalBlockTime / item.Issues 
            : 0;
        
        result.push({{
            Project: item.Project,
            Team: item.Team,
            Issues: item.Issues,
            StoryPoints: item.StoryPoints,
            TotalCycleDays: Math.round(item.TotalCycleDays * 10) / 10,
            TotalBlockTime: Math.round(item.TotalBlockTime * 10) / 10,
            AvgCycleTime: Math.round(avgCycleTime * 10) / 10,
            AvgBlockTime: Math.round(avgBlockTime * 10) / 10,
            XianFasterPct: 0,
            XianIssuesPct: 0,
            XianPointsPct: 0
        }});
        
        if (!projectTeams[item.Project]) {{
            projectTeams[item.Project] = {{}};
        }}
        projectTeams[item.Project][item.Team] = {{
            AvgCycleTime: avgCycleTime,
            Issues: item.Issues,
            StoryPoints: item.StoryPoints
        }};
        
        // Calculate project totals (excluding Overall)
        if (item.Team !== 'Overall') {{
            if (!projectTotals[item.Project]) {{
                projectTotals[item.Project] = {{
                    Issues: 0,
                    StoryPoints: 0,
                    TotalCycleDays: 0
                }};
            }}
            projectTotals[item.Project].Issues += item.Issues;
            projectTotals[item.Project].StoryPoints += item.StoryPoints;
            projectTotals[item.Project].TotalCycleDays += item.TotalCycleDays;
        }}
    }});
    
    // Second pass: calculate percentages
    result.forEach(item => {{
        // Calculate Xian Faster % compared to Overall
        if (item.Team === 'Overall' && projectTeams[item.Project]['Xian']) {{
            const overallTime = item.AvgCycleTime;
            const xianTime = projectTeams[item.Project]['Xian'].AvgCycleTime;
            if (overallTime > 0 && xianTime > 0) {{
                const fasterPct = ((overallTime - xianTime) / overallTime * 100);
                result.forEach(r => {{
                    if (r.Project === item.Project) {{
                        r.XianFasterPct = Math.round(fasterPct);
                    }}
                }});
            }}
        }}
        
        // Calculate Xian completion percentages
        if (item.Team === 'Xian') {{
            const totalIssues = projectTotals[item.Project].Issues;
            const totalPoints = projectTotals[item.Project].StoryPoints;
            item.XianIssuesPct = totalIssues > 0 ? Math.round((item.Issues / totalIssues) * 100) : 0;
            item.XianPointsPct = totalPoints > 0 ? Math.round((item.StoryPoints / totalPoints) * 100) : 0;
        }}
    }});
    
    // Debug: print Block Time Rate values
    console.log("Block Time Debug:");
    result.forEach(r => {{
        console.log(`${{r.Project}} - ${{r.Team}}: AvgBlockTime=${{r.AvgBlockTime}}`);
    }});
    
    return result;
}}

function updateSummaryStats(data) {{
    // 获取 Sydney 和 Xian 团队数据（用于 Issues/Points 统计）
    const teamData = data.filter(d => d.Team === 'Sydney' || d.Team === 'Xian');
    
    // 获取 Overall 数据
    const overallData = data.filter(d => d.Team === 'Overall');
    
    const projects = new Set(teamData.map(d => d.Project)).size;
    const totalIssues = teamData.reduce((sum, d) => sum + d.Issues, 0);
    const totalPoints = teamData.reduce((sum, d) => sum + d.StoryPoints, 0);
    
    const totalCycleDays = teamData.reduce((sum, d) => sum + (d.TotalCycleDays || 0), 0);
    const avgCycleTime = totalIssues > 0 ? (totalCycleDays / totalIssues).toFixed(1) : 0;
    
    // ===== 使用与 Table TOTAL 行完全相同的算法 =====
    // 汇总 Sydney + Xian 的数据（与 Table 的 teamData 一致）
    let totalBlockTime = 0;
    let totalXianBlockTime = 0;
    let totalXianIssues = 0;
    
    teamData.forEach(item => {{
        totalBlockTime += item.TotalBlockTime || 0;
        if (item.Team === 'Xian') {{
            totalXianBlockTime += item.TotalBlockTime || 0;
            totalXianIssues += item.Issues || 0;
        }}
    }});
    
    // 使用与 Table 完全相同的计算步骤
    const totalAvgBlockTimeRaw = totalIssues > 0 ? totalBlockTime / totalIssues : 0;
    const totalAvgBlockTimeDisplay = totalAvgBlockTimeRaw.toFixed(1);
    const xianAvgBlockTimeRaw = totalXianIssues > 0 ? totalXianBlockTime / totalXianIssues : 0;
    const xianAvgBlockTimeDisplay = xianAvgBlockTimeRaw.toFixed(1);
    
    // 使用四舍五入后的值计算百分比（与 Table 完全一致）
    const xianLessBlockedPct = totalAvgBlockTimeDisplay > 0 
        ? Math.round(((parseFloat(totalAvgBlockTimeDisplay) - parseFloat(xianAvgBlockTimeDisplay)) / parseFloat(totalAvgBlockTimeDisplay)) * 100)
        : 0;
    
    console.log("=== Card Block Time Calculation (same as Table TOTAL row) ===");
    console.log(`  totalBlockTime: ${{totalBlockTime}}`);
    console.log(`  totalIssues: ${{totalIssues}}`);
    console.log(`  totalAvgBlockTimeRaw: ${{totalAvgBlockTimeRaw}}`);
    console.log(`  totalAvgBlockTimeDisplay: ${{totalAvgBlockTimeDisplay}}`);
    console.log(`  totalXianBlockTime: ${{totalXianBlockTime}}`);
    console.log(`  totalXianIssues: ${{totalXianIssues}}`);
    console.log(`  xianAvgBlockTimeRaw: ${{xianAvgBlockTimeRaw}}`);
    console.log(`  xianAvgBlockTimeDisplay: ${{xianAvgBlockTimeDisplay}}`);
    console.log(`  xianLessBlockedPct: ${{xianLessBlockedPct}}%`);
    
    // Xian 完成百分比
    const xianIssues = teamData.filter(d => d.Team === 'Xian').reduce((sum, d) => sum + d.Issues, 0);
    const xianPoints = teamData.filter(d => d.Team === 'Xian').reduce((sum, d) => sum + d.StoryPoints, 0);
    
    const xianIssuesPct = totalIssues > 0 ? Math.round((xianIssues / totalIssues) * 100) : 0;
    const xianPointsPct = totalPoints > 0 ? Math.round((xianPoints / totalPoints) * 100) : 0;
    
    // Xian Faster % (从 Overall 行获取)
    let totalFasterPct = 0;
    let validProjectCount = 0;
    
    overallData.forEach(item => {{
        const hasXian = teamData.some(d => d.Project === item.Project && d.Team === 'Xian' && d.Issues > 0);
        if (hasXian && item.XianFasterPct !== 0) {{
            totalFasterPct += item.XianFasterPct;
            validProjectCount++;
        }}
    }});
    
    const avgFasterPct = validProjectCount > 0 ? Math.round(totalFasterPct / validProjectCount) : 0;
    
    // 更新 DOM
    document.getElementById('totalProjects').textContent = projects;
    document.getElementById('totalIssues').textContent = totalIssues;
    document.getElementById('totalPoints').textContent = totalPoints;
    document.getElementById('avgCycleTime').textContent = avgCycleTime;
    document.getElementById('avgBlockTime').textContent = totalAvgBlockTimeDisplay;
    document.getElementById('xianIssuesPct').textContent = xianIssuesPct + '%';
    document.getElementById('xianPointsPct').textContent = xianPointsPct + '%';
    document.getElementById('xianFasterPct').textContent = (avgFasterPct > 0 ? '+' : '') + avgFasterPct + '%';
    
    const lessBlockedDisplay = xianLessBlockedPct > 0 ? `+${{xianLessBlockedPct}}%` : `${{xianLessBlockedPct}}%`;
    document.getElementById('xianLessBlockedPct').textContent = lessBlockedDisplay;
}}

function updateChartTitles() {{
    const selectedArray = Array.from(selectedQuarters).sort();
    let titleSuffix = '';
    
    if (selectedArray.length === 0 || selectedArray.length === allQuarters.length) {{
        titleSuffix = '';
    }} else {{
        const quartersList = selectedArray.join(', ');
        titleSuffix = ` in ${{quartersList}}`;
    }}
    
    // 更新主图表标题 - 添加安全检查
    const issuesChartElem = document.getElementById('issuesChart');
    const pointsChartElem = document.getElementById('pointsChart');
    const cycleChartElem = document.getElementById('cycleChart');
    const avgBlockTimeChartElem = document.getElementById('avgBlockTimeChart');
    const monthlyIssuesChartElem = document.getElementById('monthlyIssuesChart');
    const monthlyPointsChartElem = document.getElementById('monthlyPointsChart');
    
    if (issuesChartElem && issuesChartElem.closest('.chart-container')) {{
        const issuesTitle = issuesChartElem.closest('.chart-container').querySelector('h2');
        if (issuesTitle) issuesTitle.innerHTML = `<span>📈</span> Issues Completed by Project${{titleSuffix}}`;
    }}
    
    if (pointsChartElem && pointsChartElem.closest('.chart-container')) {{
        const pointsTitle = pointsChartElem.closest('.chart-container').querySelector('h2');
        if (pointsTitle) pointsTitle.innerHTML = `<span>📊</span> Story Points Delivered by Project${{titleSuffix}}`;
    }}
    
    if (cycleChartElem && cycleChartElem.closest('.chart-container')) {{
        const cycleTitle = cycleChartElem.closest('.chart-container').querySelector('h2');
        if (cycleTitle) cycleTitle.innerHTML = `<span>⏱️</span> Cycle Time & Xian Faster % (vs Overall)${{titleSuffix}}`;
    }}
    
    if (avgBlockTimeChartElem && avgBlockTimeChartElem.closest('.chart-container')) {{
        const avgBlockTimeTitle = document.querySelector('#avgBlockTimeChart').closest('.chart-container').querySelector('h2');
        if (avgBlockTimeTitle) {{
            avgBlockTimeTitle.innerHTML = `<span>⏱️</span> Avg Block Time & Xian Less Blocked % (vs Overall)${{titleSuffix}}`;
        }}
    }}
    
    if (monthlyIssuesChartElem && monthlyIssuesChartElem.closest('.chart-container')) {{
        const monthlyIssuesTitle = monthlyIssuesChartElem.closest('.chart-container').querySelector('h2');
        if (monthlyIssuesTitle) monthlyIssuesTitle.innerHTML = `<span>📈</span> Monthly Completed Issues by Xian${{titleSuffix}}`;
    }}
    
    if (monthlyPointsChartElem && monthlyPointsChartElem.closest('.chart-container')) {{
        const monthlyPointsTitle = monthlyPointsChartElem.closest('.chart-container').querySelector('h2');
        if (monthlyPointsTitle) monthlyPointsTitle.innerHTML = `<span>📊</span> Monthly Completed Story Points by Xian${{titleSuffix}}`;
    }}
    
    const dataTableTitle = document.querySelector('.data-table h2');
    if (dataTableTitle) {{
        dataTableTitle.innerHTML = `<span>📋</span> Detailed Data${{titleSuffix}}`;
    }}
}}

function filterMonthlyDataByQuarters(selectedQuartersSet) {{
    const selectedArray = Array.from(selectedQuartersSet);
    
    if (selectedArray.length === 0 || selectedArray.length === allQuarters.length) {{
        return monthlyData; // Return all monthly data
    }}
    
    // Get all months that belong to selected quarters
    const selectedMonths = new Set();
    selectedArray.forEach(quarter => {{
        // Find all projects in this quarter to get the months
        const quarterProjects = quarterlyData.filter(d => d.Quarter === quarter);
        quarterProjects.forEach(item => {{
            // Extract month from the data - this assumes monthly data is linked through the project data
            // You may need to adjust this logic based on your data structure
            const month = item.Quarter; // Placeholder - needs actual implementation
        }});
    }});
    
    // For now, return all monthly data (placeholder)
    // This needs to be implemented based on your actual data structure
    return monthlyData;
}}

function updateMonthlyCharts() {{
    // Get selected quarters
    const selectedArray = Array.from(selectedQuarters);
    
    console.log("Selected quarters:", selectedArray);
    console.log("All monthly data:", monthlyData.map(d => ({{month: d.MonthLabel, issues: d.Issues, points: d.StoryPoints}})));
    
    // Filter monthly data based on selected quarters
    let filteredMonthly = monthlyData;
    
    if (selectedArray.length > 0 && selectedArray.length < allQuarters.length) {{
    
        filteredMonthly = monthlyData.filter(d => {{
            const monthLabel = d.MonthLabel; // e.g. "Jan 2026", "Feb 2026"
            
            // Check the chosed quarter
            for (let i = 0; i < selectedArray.length; i++) {{
                const quarter = selectedArray[i];
                
                if (quarter === "2025Q3" && (monthLabel.includes("Jul") || monthLabel.includes("Aug") || monthLabel.includes("Sep"))) {{
                    console.log(`Month ${{monthLabel}} matched with ${{quarter}}`);
                    return true;
                }}
                if (quarter === "2025Q4" && (monthLabel.includes("Oct") || monthLabel.includes("Nov") || monthLabel.includes("Dec"))) {{
                    console.log(`Month ${{monthLabel}} matched with ${{quarter}}`);
                    return true;
                }}
                if (quarter === "2026Q1" && (monthLabel.includes("Jan") || monthLabel.includes("Feb") || monthLabel.includes("Mar"))) {{
                    console.log(`Month ${{monthLabel}} matched with ${{quarter}}`);
                    return true;
                }}
                if (quarter === "2026Q2" && (monthLabel.includes("Apr") || monthLabel.includes("May") || monthLabel.includes("Jun"))) {{
                    console.log(`Month ${{monthLabel}} matched with ${{quarter}}`);
                    return true;
                }}
                if (quarter === "2026Q3" && (monthLabel.includes("Jul") || monthLabel.includes("Aug") || monthLabel.includes("Sep"))) {{
                    console.log(`Month ${{monthLabel}} matched with ${{quarter}}`);
                    return true;
                }}
                if (quarter === "2026Q4" && (monthLabel.includes("Oct") || monthLabel.includes("Nov") || monthLabel.includes("Dec"))) {{
                    console.log(`Month ${{monthLabel}} matched with ${{quarter}}`);
                    return true;
                }}
            }}
            return false;
        }});
    }}
    
    console.log("Filtered monthly data:", filteredMonthly.map(d => ({{month: d.MonthLabel, issues: d.Issues, points: d.StoryPoints}})));
    
    // Prepare data for charts
    const months = filteredMonthly.map(d => d.MonthLabel);
    const issuesData = filteredMonthly.map(d => d.Issues);
    const pointsData = filteredMonthly.map(d => d.StoryPoints);
    
    // Calculate averages
    const avgIssues = issuesData.length > 0 
        ? Math.round(issuesData.reduce((a, b) => a + b, 0) / issuesData.length) 
        : 0;
    const avgPoints = pointsData.length > 0 
        ? Math.round(pointsData.reduce((a, b) => a + b, 0) / pointsData.length) 
        : 0;
    
    // Destroy existing charts
    if (monthlyIssuesChart) monthlyIssuesChart.destroy();
    if (monthlyPointsChart) monthlyPointsChart.destroy();
    
    // Create a chart when having data
    if (filteredMonthly.length > 0) {{
        // Create monthly issues chart with average line
        monthlyIssuesChart = new Chart(document.getElementById('monthlyIssuesChart'), {{
            type: 'bar',
            data: {{
                labels: months,
                datasets: [{{
                    label: 'Issues Completed',
                    data: issuesData,
                    backgroundColor: '#6B9E78',
                    borderColor: '#5A8A66',
                    borderWidth: 2,
                    borderRadius: 3,
                    barPercentage: 0.7,
                    categoryPercentage: 0.8,
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: true,
                aspectRatio: 2.5,         
                plugins: {{
                    legend: {{ display: false }},
                    datalabels: {{
                        display: true,
                        anchor: 'end',
                        align: 'start',
                        offset: 4,
                        color: '#fff',
                        font: {{ weight: '600', size: 11 }},
                        formatter: value => value > 0 ? value : ''
                    }},
                    annotation: {{
                        annotations: {{
                            avgLineIssues: {{
                                type: 'line',
                                yMin: avgIssues,
                                yMax: avgIssues,
                                borderColor: '#634F7D',
                                borderWidth: 2,
                                borderDash: [12, 4, 3, 4],
                                label: {{
                                    display: true,
                                    content: `Avg: ${{avgIssues}}`,
                                    position: 'end',
                                    backgroundColor: '#634F7D',
                                    color: '#fff',
                                    font: {{
                                        size: 11,
                                        weight: 'bold'
                                    }}
                                }}
                            }}
                        }}
                    }}
                }},
                scales: {{
                    y: {{
                        beginAtZero: true,
                        grid: {{ color: '#e1e5e9', drawBorder: false }},
                        ticks: {{ stepSize: 50 }},
                        title: {{ display: true, text: 'Number of Issues', color: '#7f8c8d', font: {{ size: 12 }} }}
                    }},
                    x: {{ grid: {{ display: false }} }}
                }}
            }}
        }});
        
        // Create monthly points chart with average line
        monthlyPointsChart = new Chart(document.getElementById('monthlyPointsChart'), {{
            type: 'bar',
            data: {{
                labels: months,
                datasets: [{{
                    label: 'Story Points Completed',
                    data: pointsData,
                    backgroundColor: '#47A1AD',
                    borderColor: '#3D8F99',
                    borderWidth: 2,
                    borderRadius: 3,
                    barPercentage: 0.7,
                    categoryPercentage: 0.8,
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: true,
                aspectRatio: 2.5,         
                plugins: {{
                    legend: {{ display: false }},
                    datalabels: {{
                        display: true,
                        anchor: 'end',
                        align: 'start',
                        offset: 4,
                        color: '#fff',
                        font: {{ weight: '600', size: 11 }},
                        formatter: value => value > 0 ? value : ''
                    }},
                    annotation: {{
                        annotations: {{
                            avgLinePoints: {{
                                type: 'line',
                                yMin: avgPoints,
                                yMax: avgPoints,
                                borderColor: '#634F7D',
                                borderWidth: 2,
                                borderDash: [12, 4, 3, 4],
                                label: {{
                                    display: true,
                                    content: `Avg: ${{avgPoints}}`,
                                    position: 'end',
                                    backgroundColor: '#634F7D',
                                    color: '#fff',
                                    font: {{
                                        size: 11,
                                        weight: 'bold'
                                    }}
                                }}
                            }}
                        }}
                    }}
                }},
                scales: {{
                    y: {{
                        beginAtZero: true,
                        grid: {{ color: '#e1e5e9', drawBorder: false }},
                        ticks: {{ stepSize: 100 }},
                        title: {{ display: true, text: 'Story Points', color: '#7f8c8d', font: {{ size: 12 }} }}
                    }},
                    x: {{ grid: {{ display: false }} }}
                }}
            }}
        }});
    }} else {{
        // when no data, it will create blank chart 
        console.log("No data for selected quarters, creating empty charts");
        
        monthlyIssuesChart = new Chart(document.getElementById('monthlyIssuesChart'), {{
            type: 'bar',
            data: {{
                labels: ['No Data'],
                datasets: [{{
                    label: 'Issues Completed',
                    data: [0],
                    backgroundColor: '#48a1ad',
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{ display: false }},
                    datalabels: {{ display: false }}
                }}
            }}
        }});
        
        monthlyPointsChart = new Chart(document.getElementById('monthlyPointsChart'), {{
            type: 'bar',
            data: {{
                labels: ['No Data'],
                datasets: [{{
                    label: 'Story Points Completed',
                    data: [0],
                    backgroundColor: '#6b9f78',
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{ display: false }},
                    datalabels: {{ display: false }}
                }}
            }}
        }});
    }}
}}

function applyFilter() {{
    document.body.style.cursor = 'wait';
    
    try {{
        currentData = aggregateData(selectedQuarters);
        updateTable();
        updateSummaryStats(currentData);
        drawCharts();
        updateMonthlyCharts();
        updateChartTitles();
    }} catch (error) {{
        console.error("Error in applyFilter:", error);
    }} finally {{
        document.body.style.cursor = 'default';
    }}
}}

function clearFilter() {{
    selectedQuarters.clear();
    document.querySelectorAll('.quarter-btn').forEach(btn => {{
        btn.classList.remove('selected');
    }});
    updateActiveFilters();
    currentData = aggregateData(selectedQuarters);
    updateTable();
    updateSummaryStats(currentData);
    drawCharts();
    updateMonthlyCharts();
    updateChartTitles();
}}

function selectAllQuarters() {{
    selectedQuarters.clear();
    allQuarters.forEach(q => selectedQuarters.add(q));
    document.querySelectorAll('.quarter-btn').forEach(btn => {{
        btn.classList.add('selected');
    }});
    updateActiveFilters();
    applyFilter();
}}

function sortTable(column) {{
    if (sortColumn === column) {{
        sortAscending = !sortAscending;
    }} else {{
        sortColumn = column;
        sortAscending = true;
    }}
    updateTable();
}}

function preparePointsChart() {{
    const projects = [...new Set(currentData.map(d => d.Project))].sort();
    
    const overallData = projects.map(p => {{
        const row = currentData.find(r => r.Project === p && r.Team === 'Overall');
        return row ? Number(row['StoryPoints']) : 0;
    }});
    
    const sydneyData = projects.map(p => {{
        const row = currentData.find(r => r.Project === p && r.Team === 'Sydney');
        return row ? Number(row['StoryPoints']) : 0;
    }});
    
    const xianData = projects.map(p => {{
        const row = currentData.find(r => r.Project === p && r.Team === 'Xian');
        return row ? Number(row['StoryPoints']) : 0;
    }});
    
    const completionPctData = projects.map(p => {{
        const row = currentData.find(r => r.Project === p && r.Team === 'Xian');
        return row ? Number(row['XianPointsPct']) : 0;
    }});
    
    // Find max percentage for scaling bubble sizes
    const maxPct = Math.max(...completionPctData, 1);
    
    // Create bubble data
    const bubbleData = projects.map((project, index) => {{
        const pct = completionPctData[index];
        // Calculate bubble size proportional to percentage (min 8, max 25)
        const size = 8 + (pct / maxPct) * 17;
        return {{
            x: index + 1.5, // Position after the three bars (Overall=0.5, Sydney=1.0, Xian=1.5)
            y: 50, // Fixed position at 50% on the right axis
            r: size,
            pct: pct,
            project: project
        }};
    }});
    
    return {{
        labels: projects,
        datasets: [
            {{
                label: 'Overall Story Points',
                data: overallData,
                backgroundColor: '#634F7D',
                borderColor: '#663366',
                borderWidth: 2,
                borderRadius: 3,
                barPercentage: 0.85,
                categoryPercentage: 0.85,
                yAxisID: 'y',
            }},
            {{
                label: 'Sydney Story Points',
                data: sydneyData,
                backgroundColor: '#CC850A',
                borderColor: '#e67e22',
                borderWidth: 2,
                borderRadius: 3,
                barPercentage: 0.85,
                categoryPercentage: 0.85,
                yAxisID: 'y',
            }},
            {{
                label: 'Xian Story Points',
                data: xianData,
                backgroundColor: '#47A1AD',
                borderColor: '#3D8F99',
                borderWidth: 2,
                borderRadius: 3,
                barPercentage: 0.85,
                categoryPercentage: 0.85,
                yAxisID: 'y',
            }},
            {{
                label: 'Xian Completion %',
                data: bubbleData,
                type: 'bubble',
                backgroundColor: '#6B9E78',
                borderColor: '#5A8A66',
                borderWidth: 2,
                yAxisID: 'y1',
            }}
        ]
    }};
}}

function prepareIssuesChart() {{
    const projects = [...new Set(currentData.map(d => d.Project))].sort();
    
    const overallData = projects.map(p => {{
        const row = currentData.find(r => r.Project === p && r.Team === 'Overall');
        return row ? Number(row['Issues']) : 0;
    }});
    
    const sydneyData = projects.map(p => {{
        const row = currentData.find(r => r.Project === p && r.Team === 'Sydney');
        return row ? Number(row['Issues']) : 0;
    }});
    
    const xianData = projects.map(p => {{
        const row = currentData.find(r => r.Project === p && r.Team === 'Xian');
        return row ? Number(row['Issues']) : 0;
    }});
    
    const completionPctData = projects.map(p => {{
        const row = currentData.find(r => r.Project === p && r.Team === 'Xian');
        return row ? Number(row['XianIssuesPct']) : 0;
    }});
    
    // Find max percentage for scaling bubble sizes
    const maxPct = Math.max(...completionPctData, 1);
    
    // Create bubble data
    const bubbleData = projects.map((project, index) => {{
        const pct = completionPctData[index];
        // Calculate bubble size proportional to percentage (min 8, max 25)
        const size = 8 + (pct / maxPct) * 17;
        return {{
            x: index + 1.5, // Position after the three bars (Overall=0.5, Sydney=1.0, Xian=1.5)
            y: 50, // Fixed position at 50% on the right axis
            r: size,
            pct: pct,
            project: project
        }};
    }});
    
    return {{
        labels: projects,
        datasets: [
            {{
                label: 'Overall Issues',
                data: overallData,
                backgroundColor: '#634F7D',
                borderColor: '#663366',
                borderWidth: 2,
                borderRadius: 3,
                barPercentage: 0.85,
                categoryPercentage: 0.85,
                yAxisID: 'y',
            }},
            {{
                label: 'Sydney Issues',
                data: sydneyData,
                backgroundColor: '#CC850A',
                borderColor: '#e67e22',
                borderWidth: 2,
                borderRadius: 3,
                barPercentage: 0.85,
                categoryPercentage: 0.85,
                yAxisID: 'y',
            }},
            {{
                label: 'Xian Issues',
                data: xianData,
                backgroundColor: '#47A1AD',
                borderColor: '#3D8F99',
                borderWidth: 2,
                borderRadius: 3,
                barPercentage: 0.85,
                categoryPercentage: 0.85,
                yAxisID: 'y',
            }},
            {{
                label: 'Xian Completion %',
                data: bubbleData,
                type: 'bubble',
                backgroundColor: '#6B9E78',
                borderColor: '#5A8A66',
                borderWidth: 2,
                yAxisID: 'y1',
            }}
        ]
    }};
}}

function prepareCycleChart() {{
    const projects = [...new Set(currentData.map(d => d.Project))].sort();
    
    const overallData = projects.map(p => {{
        const row = currentData.find(r => r.Project === p && r.Team === 'Overall');
        return row ? Number(row['AvgCycleTime']) : 0;
    }});
    
    const sydneyData = projects.map(p => {{
        const row = currentData.find(r => r.Project === p && r.Team === 'Sydney');
        return row ? Number(row['AvgCycleTime']) : 0;
    }});
    
    const xianData = projects.map(p => {{
        const row = currentData.find(r => r.Project === p && r.Team === 'Xian');
        return row ? Number(row['AvgCycleTime']) : 0;
    }});
    
    const fasterPctData = projects.map(p => {{
        const row = currentData.find(r => r.Project === p && r.Team === 'Overall');
        return row ? Number(row['XianFasterPct']) : 0;
    }});
    
    return {{
        labels: projects,
        datasets: [
            {{
                label: 'Overall Cycle Time',
                data: overallData,
                backgroundColor: '#634F7D',
                borderColor: '#663366',
                borderWidth: 2,
                borderRadius: 3,
                barPercentage: 0.85,
                categoryPercentage: 0.85,
                yAxisID: 'y',
            }},
            {{
                label: 'Sydney Cycle Time',
                data: sydneyData,
                backgroundColor: '#CC850A',
                borderColor: '#e67e22',
                borderWidth: 2,
                borderRadius: 3,
                barPercentage: 0.85,
                categoryPercentage: 0.85,
                yAxisID: 'y',
            }},
            {{
                label: 'Xian Cycle Time',
                data: xianData,
                backgroundColor: '#47A1AD',
                borderColor: '#3D8F99',
                borderWidth: 2,
                borderRadius: 3,
                barPercentage: 0.85,
                categoryPercentage: 0.85,
                yAxisID: 'y',
            }},
            {{
                label: 'Xian Faster %',
                data: fasterPctData,
                type: 'bar',
                backgroundColor: fasterPctData.map(pct => 
                    pct > 0 ? '#6B9E78' : pct < 0 ? '#F2617A' : 'rgba(128, 128, 128, 0.3)'
                ),
                borderColor: fasterPctData.map(pct => 
                    pct > 0 ? '#5A8A66' : pct < 0 ? '#c0392b' : '#95a5a6'
                ),
                borderWidth: 3,
                borderRadius: 3,
                barPercentage: 3,
                categoryPercentage: -0.3,
                yAxisID: 'y1',
            }}
        ]
    }};
}}

function prepareAvgBlockTimeChart() {{
    const projects = [...new Set(currentData.map(d => d.Project))].sort();
    
    const overallAvgBlockTime = projects.map(p => {{
        const row = currentData.find(r => r.Project === p && r.Team === 'Overall');
        return row ? Number(row['AvgBlockTime']) : 0;
    }});
    
    const sydneyAvgBlockTime = projects.map(p => {{
        const row = currentData.find(r => r.Project === p && r.Team === 'Sydney');
        return row ? Number(row['AvgBlockTime']) : 0;
    }});
    
    const xianAvgBlockTime = projects.map(p => {{
        const row = currentData.find(r => r.Project === p && r.Team === 'Xian');
        return row ? Number(row['AvgBlockTime']) : 0;
    }});
    
    // 计算 Xian Less Blocked % (vs Overall)
    const xianLessBlockedPct = projects.map(p => {{
        const overallRow = currentData.find(r => r.Project === p && r.Team === 'Overall');
        const xianRow = currentData.find(r => r.Project === p && r.Team === 'Xian');
        if (overallRow && xianRow && overallRow.AvgBlockTime > 0) {{
            const pct = ((overallRow.AvgBlockTime - xianRow.AvgBlockTime) / overallRow.AvgBlockTime) * 100;
            return Math.round(pct);
        }}
        return 0;
    }});
    
    return {{
        labels: projects,
        datasets: [
            {{
                label: 'Overall Avg Block Time',
                data: overallAvgBlockTime,
                backgroundColor: '#634F7D',
                borderColor: '#663366',
                borderWidth: 2,
                borderRadius: 3,
                barPercentage: 0.85,
                categoryPercentage: 0.85,
                yAxisID: 'y',
            }},
            {{
                label: 'Sydney Avg Block Time',
                data: sydneyAvgBlockTime,
                backgroundColor: '#CC850A',
                borderColor: '#e67e22',
                borderWidth: 2,
                borderRadius: 3,
                barPercentage: 0.85,
                categoryPercentage: 0.85,
                yAxisID: 'y',
            }},
            {{
                label: 'Xian Avg Block Time',
                data: xianAvgBlockTime,
                backgroundColor: '#47A1AD',
                borderColor: '#3D8F99',
                borderWidth: 2,
                borderRadius: 3,
                barPercentage: 0.85,
                categoryPercentage: 0.85,
                yAxisID: 'y',
            }},
            {{
                label: 'Xian Less Blocked %',
                data: xianLessBlockedPct,
                type: 'bar',
                backgroundColor: xianLessBlockedPct.map(pct => 
                    pct > 0 ? '#6B9E78' : pct < 0 ? '#F2617A' : 'rgba(128, 128, 128, 0.3)'
                ),
                borderColor: xianLessBlockedPct.map(pct => 
                    pct > 0 ? '#5A8A66' : pct < 0 ? '#c0392b' : '#95a5a6'
                ),
                borderWidth: 3,
                borderRadius: 3,
                barPercentage: 3,
                categoryPercentage: -0.3,
                yAxisID: 'y1',
            }}
        ]
    }};
}}

function updateTable() {{
    const tbody = document.getElementById('tableBody');
    const tfoot = document.getElementById('tableFooter');
    tbody.innerHTML = '';
    tfoot.innerHTML = '';
    
    const projects = [...new Set(currentData.map(d => d.Project))].sort();
    
    // Calculate totals
    let totalIssues = 0;
    let totalPoints = 0;
    let totalCycleDays = 0;
    let totalBlockTime = 0;
    let totalXianIssues = 0;
    let totalXianPoints = 0;
    let totalXianBlockTime = 0;  // 添加这一行
    let totalFasterPct = 0;
    let fasterPctCount = 0;
    
    for (let p = 0; p < projects.length; p++) {{
        const project = projects[p];
        const projectRows = currentData
            .filter(d => d.Project === project)
            .sort((a, b) => {{
                const teamOrder = {{'Overall': 0, 'Sydney': 1, 'Xian': 2}};
                return teamOrder[a.Team] - teamOrder[b.Team];
            }});

        // Find Xian Faster % for this project (from Overall row)
        const overallRow = projectRows.find(r => r.Team === 'Overall');
        if (overallRow && overallRow.XianFasterPct !== 0) {{
            totalFasterPct += overallRow.XianFasterPct;
            fasterPctCount++;
        }}    
        
        // Calculate project totals for footer
        const projectXian = projectRows.find(r => r.Team === 'Xian');
        const projectSydney = projectRows.find(r => r.Team === 'Sydney');
        
        if (projectXian) {{
            totalXianIssues += projectXian.Issues;
            totalXianPoints += projectXian.StoryPoints;
            totalXianBlockTime += projectXian.TotalBlockTime || 0;  // 添加这一行
            totalBlockTime += projectXian.TotalBlockTime || 0;
        }}
        
        if (projectSydney) {{
            totalIssues += projectSydney.Issues;
            totalPoints += projectSydney.StoryPoints;
            totalCycleDays += projectSydney.TotalCycleDays;
            totalBlockTime += projectSydney.TotalBlockTime || 0;
        }}
        
        if (projectXian) {{
            totalIssues += projectXian.Issues;
            totalPoints += projectXian.StoryPoints;
            totalCycleDays += projectXian.TotalCycleDays;
        }}
        
        for (let r = 0; r < projectRows.length; r++) {{
            const row = projectRows[r];
            const tr = document.createElement('tr');
            
            // Get Block Time display (keep a decimal)
            let blockTimeDisplay = '';
            if (row.TotalBlockTime && row.TotalBlockTime > 0) {{
                blockTimeDisplay = row.TotalBlockTime.toFixed(1);
            }}
            
            // Calculate Avg Block Time
            let avgBlockTimeDisplay = '';
            if (row.TotalBlockTime && row.Issues > 0) {{
                avgBlockTimeDisplay = (row.TotalBlockTime / row.Issues).toFixed(1);
            }}

            // Calculate Xian Less Blocked % (Only display in Overall line)
            let lessBlockedDisplay = '';
            if (row.Team === 'Overall') {{
                const xianRow = projectRows.find(r => r.Team === 'Xian');
                if (xianRow && xianRow.AvgBlockTime > 0 && row.AvgBlockTime > 0) {{
                    const pct = ((row.AvgBlockTime - xianRow.AvgBlockTime) / row.AvgBlockTime) * 100;
                    lessBlockedDisplay = (pct > 0 ? '+' : '') + Math.round(pct) + '%';
                }}
            }}
            
            if (r === 0) {{
                let pctClass = '';
                let pctDisplay = '';
                if (row.Team === 'Overall') {{
                    pctClass = row.XianFasterPct > 0 ? 'positive' : (row.XianFasterPct < 0 ? 'negative' : '');
                    pctDisplay = row.XianFasterPct + '%';
                }}
                
                let issuesPct = '';
                let pointsPct = '';
                if (row.Team === 'Xian') {{
                    issuesPct = row.XianIssuesPct + '%';
                    pointsPct = row.XianPointsPct + '%';
                }}
                
                tr.innerHTML = `
                    <td><strong>${{row.Project}}</strong></td>
                    <td>${{row.Team}}</td>
                    <td>${{row.Issues}}</td>
                    <td>${{issuesPct}}</td>
                    <td>${{row.StoryPoints}}</td>
                    <td>${{pointsPct}}</td>
                    <td>${{row.TotalCycleDays.toFixed(1)}}</td>
                    <td>${{row.AvgCycleTime.toFixed(1)}}</td>
                    <td class="${{pctClass}}">${{pctDisplay}}</td>
                    <td>${{blockTimeDisplay}}</td>
                    <td>${{avgBlockTimeDisplay}}</td>
                    <td>${{lessBlockedDisplay}}</td>
                `;
            }} else {{
                let issuesPct = '';
                let pointsPct = '';
                if (row.Team === 'Xian') {{
                    issuesPct = row.XianIssuesPct + '%';
                    pointsPct = row.XianPointsPct + '%';
                }}
                
                tr.innerHTML = `
                    <td></td>
                    <td>${{row.Team}}</td>
                    <td>${{row.Issues}}</td>
                    <td>${{issuesPct}}</td>
                    <td>${{row.StoryPoints}}</td>
                    <td>${{pointsPct}}</td>
                    <td>${{row.TotalCycleDays.toFixed(1)}}</td>
                    <td>${{row.AvgCycleTime.toFixed(1)}}</td>
                    <td></td>
                    <td>${{blockTimeDisplay}}</td>
                    <td>${{avgBlockTimeDisplay}}</td>
                    <td>${{lessBlockedDisplay}}</td>
                `;
            }}
            
            if (row.Team === 'Overall') {{
                tr.classList.add('overall-row');
            }}
            
            tbody.appendChild(tr);
        }}
        
        if (p < projects.length - 1) {{
            const spacer = document.createElement('tr');
            spacer.innerHTML = '<td colspan="12" style="padding: 5px; background: transparent;"></td>';
            tbody.appendChild(spacer);
        }}
    }}

    // Calculate percentages
    const xianIssuesPct = totalIssues > 0 ? Math.round((totalXianIssues / totalIssues) * 100) : 0;
    const xianPointsPct = totalPoints > 0 ? Math.round((totalXianPoints / totalPoints) * 100) : 0;
    const avgCycleTimeValue = totalIssues > 0 ? (totalCycleDays / totalIssues).toFixed(1) : 0;
    
    // Calculate average Xian Faster % (simple average of project percentages)
    const avgFasterPct = fasterPctCount > 0 ? Math.round(totalFasterPct / fasterPctCount) : 0;
    
    // Calculate TOTAL Average Block Time (weighted average)
    const totalAvgBlockTime = totalIssues > 0 ? (totalBlockTime / totalIssues).toFixed(1) : 0;
    const xianAvgBlockTime = totalXianIssues > 0 ? (totalXianBlockTime / totalXianIssues).toFixed(1) : 0;

    // Calculate Xian Less Blocked %
    const xianLessBlockedTotal = totalAvgBlockTime > 0 
        ? Math.round(((totalAvgBlockTime - xianAvgBlockTime) / totalAvgBlockTime) * 100) : 0;
    
    const totalRow = document.createElement('tr');
    totalRow.className = 'total-row';
    totalRow.innerHTML = `
        <td><strong>TOTAL</strong></td>
        <td></td>
        <td><strong>${{totalIssues}}</strong></td>
        <td><strong>${{xianIssuesPct}}%</strong></td>
        <td><strong>${{totalPoints}}</strong></td>
        <td><strong>${{xianPointsPct}}%</strong></td>
        <td><strong>${{totalCycleDays.toFixed(1)}}</strong></td>
        <td><strong>${{avgCycleTimeValue}}</strong></td>
        <td><strong class="${{avgFasterPct > 0 ? 'positive' : avgFasterPct < 0 ? 'negative' : ''}}">
        ${{avgFasterPct > 0 ? '+' : ''}}${{avgFasterPct}}%
        </strong></td>
        <td><strong>${{totalBlockTime.toFixed(1)}}</strong></td>
        <td><strong>${{totalAvgBlockTime}}</strong></td>
        <td><strong>${{xianLessBlockedTotal > 0 ? '+' : ''}}${{Math.round(xianLessBlockedTotal)}}%</strong></td>
    `;
    tfoot.appendChild(totalRow);
}}

function drawCharts() {{
    if (pointsChart) pointsChart.destroy();
    if (issuesChart) issuesChart.destroy();
    if (cycleChart) cycleChart.destroy();
    if (avgBlockTimeChart) avgBlockTimeChart.destroy();
    
    if (currentData.length === 0) return;
    
    // Ensure canvas elements existing
    const pointsCanvas = document.getElementById('pointsChart');
    const issuesCanvas = document.getElementById('issuesChart');
    const cycleCanvas = document.getElementById('cycleChart');
    const avgBlockTimeCanvas = document.getElementById('avgBlockTimeChart');
    
    if (!pointsCanvas || !issuesCanvas || !cycleCanvas || !avgBlockTimeCanvas) {{
        console.error("Canvas element not found");
        return;
    }}
    
    const pointsData = preparePointsChart();
    const issuesData = prepareIssuesChart();
    const cycleData = prepareCycleChart();
    const avgBlockTimeData = prepareAvgBlockTimeChart();
    
    // Find max values for y-axis scaling
    const maxPoints = Math.max(
        ...pointsData.datasets[0].data,
        ...pointsData.datasets[1].data,
        ...pointsData.datasets[2].data
    );
    const maxIssues = Math.max(
        ...issuesData.datasets[0].data,
        ...issuesData.datasets[1].data,
        ...issuesData.datasets[2].data
    );
    const maxCycleValues = [];
    for (let i = 0; i < cycleData.datasets[0].data.length; i++) {{
        maxCycleValues.push(cycleData.datasets[0].data[i]);
        maxCycleValues.push(cycleData.datasets[1].data[i]);
        maxCycleValues.push(cycleData.datasets[2].data[i]);
    }}
    const maxCycleValue = Math.ceil(Math.max(...maxCycleValues) * 2);

    const maxAvgBlockTime = Math.max(
        ...avgBlockTimeData.datasets[0].data,
        ...avgBlockTimeData.datasets[1].data,
        ...avgBlockTimeData.datasets[2].data
    );
    
    pointsChart = new Chart(document.getElementById('pointsChart'), {{
        type: 'bar',
        data: pointsData,
        options: {{
            responsive: true,
            maintainAspectRatio: false,
            plugins: {{
                legend: {{ display: true }},
                datalabels: {{
                    display: true,
                    anchor: function(context) {{
                        if (context.dataset.label === 'Xian Completion %') {{
                            return 'center';
                        }}
                        return 'end';
                    }},
                    align: function(context) {{
                        if (context.dataset.label === 'Xian Completion %') {{
                            return 'center';
                        }}
                        return 'start';
                    }},
                    offset: function(context) {{
                        return context.dataset.label === 'Xian Completion %' ? 0 : 4;
                    }},
                    color: function(context) {{
                        return context.dataset.label === 'Xian Completion %' ? '#ffffff' : '#ffffff';
                    }},
                    font: function(context) {{
                        if (context.dataset.label === 'Xian Completion %') {{
                            return {{ weight: 'bold', size: 12 }};
                        }}
                        return {{ weight: '600', size: 11 }};
                    }},
                    formatter: function(value, context) {{
                        if (context.dataset.label === 'Xian Completion %') {{
                            return value.pct !== 0 ? value.pct + '%' : '';
                        }}
                        return value > 0 ? value : '';
                    }}
                }},
                tooltip: {{
                    callbacks: {{
                        label: function(context) {{
                            if (context.dataset.label === 'Xian Completion %') {{
                                return `Xian Completion: ${{context.raw.pct}}%`;
                            }}
                            return context.dataset.label + ': ' + context.raw;
                        }}
                    }}
                }}
            }},
            scales: {{
                y: {{
                    type: 'linear',
                    position: 'left',
                    beginAtZero: true,
                    max: Math.ceil(maxPoints * 1.5),
                    grid: {{ drawOnChartArea: false }},
                    title: {{ display: true, text: 'Story Points', color: '#7f8c8d', font: {{ size: 12 }} }},
                    ticks: {{ 
                        display: false,
                        stepSize: 100 
                        }}
                }},
                y1: {{
                    type: 'linear',
                    position: 'right',
                    beginAtZero: true,
                    min: -200,
                    max: 100,
                    grid: {{ drawOnChartArea: false }},
                    title: {{ display: true, text: 'Completion %', color: '#7f8c8d', font: {{ size: 12 }} }},
                    ticks: {{
                        display: false,
                        stepSize: 100,
                        callback: function(value) {{
                            return value + '%';
                        }}
                    }}
                }},
                x: {{ grid: {{ display: true }} }}
            }}
        }}
    }});
    
    issuesChart = new Chart(document.getElementById('issuesChart'), {{
        type: 'bar',
        data: issuesData,
        options: {{
            responsive: true,
            maintainAspectRatio: false,
            plugins: {{
                legend: {{ display: true }},
                datalabels: {{
                    display: true,
                    anchor: function(context) {{
                        if (context.dataset.label === 'Xian Completion %') {{
                            return 'center';
                        }}
                        return 'end';
                    }},
                    align: function(context) {{
                        if (context.dataset.label === 'Xian Completion %') {{
                            return 'center';
                        }}
                        return 'start';
                    }},
                    offset: function(context) {{
                        return context.dataset.label === 'Xian Completion %' ? 0 : 4;
                    }},
                    color: function(context) {{
                        return context.dataset.label === 'Xian Completion %' ? '#ffffff' : '#ffffff';
                    }},
                    font: function(context) {{
                        if (context.dataset.label === 'Xian Completion %') {{
                            return {{ weight: 'bold', size: 12 }};
                        }}
                        return {{ weight: '600', size: 11 }};
                    }},
                    formatter: function(value, context) {{
                        if (context.dataset.label === 'Xian Completion %') {{
                            return value.pct !== 0 ? value.pct + '%' : '';
                        }}
                        return value > 0 ? value : '';
                    }}
                }},
                tooltip: {{
                    callbacks: {{
                        label: function(context) {{
                            if (context.dataset.label === 'Xian Completion %') {{
                                return `Xian Completion: ${{context.raw.pct}}%`;
                            }}
                            return context.dataset.label + ': ' + context.raw;
                        }}
                    }}
                }}
            }},
            scales: {{
                y: {{
                    type: 'linear',
                    position: 'left',
                    beginAtZero: true,
                    max: Math.ceil(maxIssues * 1.5),
                    grid: {{ drawOnChartArea: false }},
                    title: {{ display: true, text: 'Number of Issues', color: '#7f8c8d', font: {{ size: 12 }} }},
                    ticks: {{ 
                        display: false,
                        stepSize: 100 
                        }}
                }},
                y1: {{
                    type: 'linear',
                    position: 'right',
                    beginAtZero: true,
                    min: -200,
                    max: 100,
                    grid: {{ drawOnChartArea: false }},
                    title: {{ display: true, text: 'Completion %', color: '#7f8c8d', font: {{ size: 12 }} }},
                    ticks: {{
                        display: false,
                        stepSize: 100,
                        callback: function(value) {{
                            return value + '%';
                        }}
                    }}
                }},
                x: {{ 
                    grid: {{ display: true }} 
                }}
            }}
        }}
    }});
    
    cycleChart = new Chart(document.getElementById('cycleChart'), {{
        type: 'bar',
        data: cycleData,
        options: {{
            responsive: true,
            maintainAspectRatio: false,
            plugins: {{
                legend: {{ display: true }},
                datalabels: {{
                    display: true,
                    anchor: 'end',
                    align: 'start',
                    offset: 4,
                    color: '#ffffff',
                    font: {{ weight: '600', size: 11 }},
                    formatter: function(value, context) {{
                        if (context.dataset.label === 'Xian Faster %') {{
                            return value !== 0 ? value + '%' : '';
                        }}
                        return value > 0 ? value.toFixed(1) : '';
                    }}
                }},
                tooltip: {{
                    callbacks: {{
                        label: function(context) {{
                            if (context.dataset.label === 'Xian Faster %') {{
                                const value = context.raw;
                                const direction = value > 0 ? 'faster' : value < 0 ? 'slower' : 'same';
                                return `Xian is ${{Math.abs(value)}}% ${{direction}} vs Overall`;
                            }}
                            return context.dataset.label + ': ' + context.raw.toFixed(1) + ' days';
                        }}
                    }}
                }}
            }},
            scales: {{
                y: {{
                    type: 'linear',
                    position: 'left',
                    beginAtZero: true,
                    max: maxCycleValue,
                    grid: {{ drawOnChartArea: false }},
                    title: {{
                        display: true,
                        text: 'Days',
                        color: '#7f8c8d',
                        font: {{ size: 12, weight: '600' }}
                    }},
                    ticks: {{
                        display: false,
                        callback: function(value) {{
                            return value;
                        }}
                    }}
                }},
                y1: {{
                    type: 'linear',
                    position: 'right',
                    beginAtZero: true,
                    min: -200,
                    max: 100,
                    grid: {{ drawOnChartArea: true }},
                    title: {{
                        display: true,
                        text: 'Xian Faster %',
                        color: '#7f8c8d',
                        font: {{ size: 12, weight: '600' }}
                    }},
                    ticks: {{
                        display: false,
                        stepSize: 100,
                        callback: function(value) {{
                            return value + '%';
                        }}
                    }}
                }},
                x: {{
                    grid: {{ display: false }},
                }}
            }}
        }}
    }});

    avgBlockTimeChart = new Chart(document.getElementById('avgBlockTimeChart'), {{
        type: 'bar',
        data: avgBlockTimeData,
        options: {{
            responsive: true,
            maintainAspectRatio: false,
            plugins: {{
                legend: {{ 
                    display: true,
                    labels: {{
                        generateLabels: function(chart) {{
                            const labels = Chart.defaults.plugins.legend.labels.generateLabels(chart);
                            labels.forEach(label => {{
                                if (label.text === 'Xian Less Blocked %') {{
                                    label.fillStyle = '#6B9E78';
                                    label.strokeStyle = '#5A8A66';
                                    label.lineWidth = 3;
                                }}
                            }});
                            return labels;
                        }}
                    }}
                }},
                datalabels: {{
                    display: true,
                    anchor: function(context) {{
                        if (context.dataset.label === 'Xian Less Blocked %') {{
                            return 'end';
                        }}
                        return 'end';
                    }},
                    align: function(context) {{
                        if (context.dataset.label === 'Xian Less Blocked %') {{
                            return 'start';
                        }}
                        return 'start';
                    }},
                    offset: 4,
                    color: function(context) {{
                        if (context.dataset.label === 'Xian Less Blocked %') {{
                            return '#ffffff';
                        }}
                        return '#ffffff';
                    }},
                    font: {{ weight: '600', size: 11 }},
                    formatter: function(value, context) {{
                        if (context.dataset.label === 'Xian Less Blocked %') {{
                            return value !== 0 ? Math.abs(value) + '%' : '';
                        }}
                        return value > 0 ? value.toFixed(1) + '' : '';
                    }}
                }},
                tooltip: {{
                    callbacks: {{
                        label: function(context) {{
                            if (context.dataset.label === 'Xian Less Blocked %') {{
                                const value = context.raw;
                                const direction = value > 0 ? 'less blocked' : value < 0 ? 'more blocked' : 'same';
                                return `Xian is ${{Math.abs(value)}}% ${{direction}} than Overall`;
                            }}
                            return `${{context.dataset.label}}: ${{context.raw.toFixed(1)}} days`;
                        }}
                    }}
                }}
            }},
            scales: {{
                y: {{
                    type: 'linear',
                    position: 'left',
                    beginAtZero: true,
                    max: Math.ceil(maxAvgBlockTime * 1.5),
                    grid: {{ drawOnChartArea: false }},
                    title: {{
                        display: true,
                        text: 'Days',
                        color: '#7f8c8d',
                        font: {{ size: 12, weight: '600' }}
                    }},
                    ticks: {{
                        display: false,
                        callback: function(value) {{
                            return value + '';
                        }}
                    }}
                }},
                y1: {{
                    type: 'linear',
                    position: 'right',
                    beginAtZero: true,
                    min: -200,
                    max: 100,
                    grid: {{ drawOnChartArea: true }},
                    title: {{
                        display: true,
                        text: 'Xian Less Blocked %',
                        color: '#7f8c8d',
                        font: {{ size: 12, weight: '600' }}
                    }},
                    ticks: {{
                        stepSize: 100,
                        display: false,
                        callback: function(value) {{
                            return value + '%';
                        }}
                    }}
                }},
                x: {{
                    grid: {{ display: false }}
                }}
            }}
        }}
    }});
}}

function downloadDashboard() {{
    // Show loading state
    const downloadBtn = document.querySelector('.download-btn');
    const originalText = downloadBtn ? downloadBtn.innerHTML : '';
    if (downloadBtn) {{
        downloadBtn.innerHTML = '<span>⏳</span> Capturing...';
        downloadBtn.disabled = true;
    }}
    
    const controls = document.querySelector('.controls');
    const dataTable = document.querySelector('.data-table');
    const originalDisplay = [];
    
    if (controls) {{
        originalDisplay.push({{el: controls, display: controls.style.display}});
        controls.style.display = 'none';
    }}
    
    if (dataTable) {{
        originalDisplay.push({{el: dataTable, display: dataTable.style.display}});
        dataTable.style.display = 'none';
    }}
    
    setTimeout(() => {{
        html2canvas(document.body, {{
            scale: 2,
            backgroundColor: '#f0f2f5',
            logging: false,
            allowTaint: false,
            useCORS: true,
            windowWidth: 1200,
        }}).then(canvas => {{
            if (controls) controls.style.display = originalDisplay[0].display;
            if (dataTable) dataTable.style.display = originalDisplay[1].display;
            
            const link = document.createElement('a');
            const dateStr = new Date().toISOString().slice(0,10);
            const quarterStr = Array.from(selectedQuarters).sort().join('_') || 'all';
            link.download = `delivery-dashboard-${{quarterStr}}-${{dateStr}}.png`;
            link.href = canvas.toDataURL('image/png');
            link.click();
            
            if (downloadBtn) {{
                downloadBtn.innerHTML = originalText;
                downloadBtn.disabled = false;
            }}
        }}).catch(error => {{
            console.error('Error capturing dashboard:', error);
            alert('Failed to download dashboard. Please try again.');
            
            if (controls) controls.style.display = originalDisplay[0].display;
            if (dataTable) dataTable.style.display = originalDisplay[1].display;
            
            if (downloadBtn) {{
                downloadBtn.innerHTML = originalText;
                downloadBtn.disabled = false;
            }}
        }});
    }}, 500);
}}

function addDownloadButton() {{
    const actionButtons = document.querySelector('.action-buttons');
    if (actionButtons) {{
        // Check if button already exists
        if (document.querySelector('.download-btn')) {{
            return;
        }}
        
        const downloadBtn = document.createElement('button');
        downloadBtn.className = 'action-btn download-btn';
        downloadBtn.innerHTML = '<span>📥</span> Download Dashboard';
        downloadBtn.onclick = downloadDashboard;
        downloadBtn.style.background = '#27ae60';
        downloadBtn.style.color = 'white';
        downloadBtn.style.marginLeft = 'auto';
        actionButtons.appendChild(downloadBtn);
    }}
}}

// Initialize
currentData = aggregateData(selectedQuarters);
updateTable();
updateSummaryStats(currentData);
drawCharts();
updateMonthlyCharts();
updateChartTitles();
updateActiveFilters();
addDownloadButton();
</script>

</body>
</html>'''

    # Save both files
    with open(OUTPUT_HTML_TIMESTAMP, 'w', encoding='utf-8') as f:
        f.write(html_content)

    with open(OUTPUT_HTML_LATEST, 'w', encoding='utf-8') as f:
        f.write(html_content)

    print("Dashboard generated:")
    print("Versioned file:", OUTPUT_HTML_TIMESTAMP)
    print("Latest file:", OUTPUT_HTML_LATEST)

    return html_content

# ===== Main =====
def main():
    jira_df = load_latest_jira_csv()
    xian_engineers = load_xian_engineers()
    jira_df = classify_team(jira_df, xian_engineers)
    jira_df = prepare_metrics(jira_df)
    
    # Get available quarters for filters
    available_quarters = sorted(jira_df['Year-Quarter'].unique())
    
    # Generate dashboard with all data
    summary, _ = aggregate_metrics_for_dashboard(jira_df)
    generate_html(summary, available_quarters, jira_df)

if __name__ == "__main__":
    main()