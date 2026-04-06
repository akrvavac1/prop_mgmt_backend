from fastapi import FastAPI, Depends, HTTPException, status
from google.cloud import bigquery
from pydantic import BaseModel
from typing import Optional
from datetime import date
import os
from fastapi.middleware.cors import CORSMiddleware
app = FastAPI()

PROJECT_ID = "sp26-mgmt54500-dev-anisa"
DATASET = "property_mgmt"

#CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Pydantic Models  
# ---------------------------------------------------------------------------

class IncomeRecord(BaseModel):
    amount: float
    source: str
    date: date
    notes: Optional[str] = None

class ExpenseRecord(BaseModel):
    amount: float
    category: str
    date: date
    vendor: Optional[str] = None
    notes: Optional[str] = None

class PropertyCreate(BaseModel):
    name: str
    address: str
    city: str
    state: str
    postal_code: str
    property_type: str
    tenant_name: Optional[str] = None
    monthly_rent: Optional[float] = None


def assert_property_exists(property_id: int, bq: bigquery.Client):
    query = f"""
        SELECT property_id FROM `{PROJECT_ID}.{DATASET}.properties`
        WHERE property_id = {property_id}
    """
    if not list(bq.query(query).result()):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Property {property_id} not found"
        )




# ---------------------------------------------------------------------------
# Dependency: BigQuery client
# ---------------------------------------------------------------------------

def get_bq_client():
    client = bigquery.Client()
    try:
        yield client
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

@app.get("/properties") # works well
def get_properties(bq: bigquery.Client = Depends(get_bq_client)):
    """
    Returns all properties in the database.
    """
    query = f"""
        SELECT
            property_id,
            name,
            address,
            city,
            state,
            postal_code,
            property_type,
            tenant_name,
            monthly_rent
        FROM `{PROJECT_ID}.{DATASET}.properties`
        ORDER BY property_id
    """

    try:
        results = bq.query(query).result()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database query failed: {str(e)}"
        )

    properties = [dict(row) for row in results]
    return properties

# second endpoint works well
@app.get("/properties/{property_id}")
def get_property(property_id: int, bq: bigquery.Client = Depends(get_bq_client)):
    """Returns a single property by ID."""
    query = f"""
        SELECT
            property_id, name, address, city, state,
            postal_code, property_type, tenant_name, monthly_rent
        FROM `{PROJECT_ID}.{DATASET}.properties`
        WHERE property_id = {property_id}
    """
    try:
        rows = list(bq.query(query).result())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database query failed: {str(e)}")
    if not rows:
        raise HTTPException(status_code=404, detail=f"Property {property_id} not found")
    return dict(rows[0])


#post - additional endpoint 1 (check)
@app.post("/properties", status_code=201)
def create_property(prop: PropertyCreate, bq: bigquery.Client = Depends(get_bq_client)):
    """Creates a new property."""
    query = f"""
        INSERT INTO `{PROJECT_ID}.{DATASET}.properties`
            (name, address, city, state, postal_code, property_type, tenant_name, monthly_rent)
        VALUES (
            '{prop.name}', '{prop.address}', '{prop.city}', '{prop.state}',
            '{prop.postal_code}', '{prop.property_type}',
            '{prop.tenant_name or ""}', {prop.monthly_rent or 0}
        )
    """
    try:
        bq.query(query).result()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Insert failed: {str(e)}")
    return {"message": "Property created successfully"}



#Income Section (needs fixing) - looks like it is fixed for now 
@app.get("/income/{property_id}")
def get_income(property_id: int, bq: bigquery.Client = Depends(get_bq_client)):
    """Returns all income records for a property."""
    assert_property_exists(property_id, bq)
    query = f"""
        SELECT income_id, property_id, amount, date, description
        FROM `{PROJECT_ID}.{DATASET}.income`
        WHERE property_id = {property_id}
        ORDER BY date DESC
    """
    # Configure the parameter to prevent SQL injection
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("property_id", "INT64", property_id)
        ]
    )


    try:
        results = bq.query(query).result()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database query failed: {str(e)}")
    return [dict(row) for row in results]


@app.post("/income/{property_id}", status_code=201)
def create_income(property_id: int, record: IncomeRecord, bq: bigquery.Client = Depends(get_bq_client)):
    """Creates a new income record for a property."""
    assert_property_exists(property_id, bq)
    query = f"""
        INSERT INTO `{PROJECT_ID}.{DATASET}.income`
            (property_id, amount, source, date, notes)
        VALUES (
            {property_id}, {record.amount}, '{record.source}',
            '{record.date}', '{record.notes or ""}'
        )
    """
    try:
        bq.query(query).result()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Insert failed: {str(e)}")
    return {"message": "Income record created"}


#additional income endpoint 
@app.get("/income/by-property-type")
def get_income_by_property_type(
    property_type: Optional[str] = None,
    bq: bigquery.Client = Depends(get_bq_client)
):
    """
    Returns total income grouped by property type across all properties.
    Example: /income/by-property-type
    Example: /income/by-property-type?property_type=multi-family
    """

    if property_type:
        # Filter to a specific property type
        query = f"""
            SELECT
                p.property_type,
                p.property_id,
                p.name,
                p.address,
                COUNT(i.income_id)  AS record_count,
                SUM(i.amount)       AS total_income
            FROM `{PROJECT_ID}.{DATASET}.properties` p
            LEFT JOIN `{PROJECT_ID}.{DATASET}.income` i ON p.property_id = i.property_id
            WHERE LOWER(p.property_type) = LOWER('{property_type}')
            GROUP BY p.property_type, p.property_id, p.name, p.address
            ORDER BY total_income DESC
        """
    else:
        # No filter — group by property type only
        query = f"""
            SELECT
                p.property_type,
                COUNT(DISTINCT p.property_id)   AS total_properties,
                COUNT(i.income_id)              AS record_count,
                COALESCE(SUM(i.amount), 0)      AS total_income,
                COALESCE(AVG(i.amount), 0)      AS avg_income_per_record
            FROM `{PROJECT_ID}.{DATASET}.properties` p
            LEFT JOIN `{PROJECT_ID}.{DATASET}.income` i ON p.property_id = i.property_id
            GROUP BY p.property_type
            ORDER BY total_income DESC
        """

    try:
        results = bq.query(query).result()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database query failed: {str(e)}")

    return [dict(row) for row in results]    








#Expense Section (needs fixing)
@app.get("/expenses/{property_id}")
def get_expenses(property_id: int, bq: bigquery.Client = Depends(get_bq_client)):
    """Returns all expense records for a property."""
    assert_property_exists(property_id, bq)
    query = f"""
        SELECT expense_id, property_id, amount, category, date, vendor,description
        FROM `{PROJECT_ID}.{DATASET}.expenses`
        WHERE property_id = {property_id}
        ORDER BY date DESC
    """
    # Configure the parameter to prevent SQL injection
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("property_id", "INT64", property_id)
        ]
    )


    try:
        results = bq.query(query).result()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database query failed: {str(e)}")
    return [dict(row) for row in results]



@app.post("/expenses/{property_id}", status_code=201)
def create_expense(property_id: int, record: ExpenseRecord, bq: bigquery.Client = Depends(get_bq_client)):
    """Creates a new expense record for a property."""
    assert_property_exists(property_id, bq)
    query = f"""
        INSERT INTO `{PROJECT_ID}.{DATASET}.expenses`
            (property_id, amount, category, date, vendor, notes)
        VALUES (
            {property_id}, {record.amount}, '{record.category}',
            '{record.date}', '{record.vendor or ""}', '{record.notes or ""}'
        )
    """
    try:
        bq.query(query).result()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Insert failed: {str(e)}")
    return {"message": "Expense record created"}


#additional expense endpoint
@app.delete("/expenses/{expense_id}")
def delete_expense(expense_id: int, bq: bigquery.Client = Depends(get_bq_client)):
    """Deletes an expense record by ID."""
    check = f"SELECT expense_id FROM `{PROJECT_ID}.{DATASET}.expenses` WHERE expense_id = {expense_id}"
    if not list(bq.query(check).result()):
        raise HTTPException(status_code=404, detail=f"Expense record {expense_id} not found")
    try:
        bq.query(f"DELETE FROM `{PROJECT_ID}.{DATASET}.expenses` WHERE expense_id = {expense_id}").result()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Delete failed: {str(e)}")
    return {"message": f"Expense record {expense_id} deleted"}




#additional endpoints (summary)
@app.get("/summary/{property_id}")
def get_property_summary(property_id: int, bq: bigquery.Client = Depends(get_bq_client)):
    """Returns total income, total expenses, and net income for a property."""
    assert_property_exists(property_id, bq)
    query = f"""
        SELECT
            p.property_id,
            p.name,
            p.address,
            COALESCE(SUM(i.amount), 0) AS total_income,
            COALESCE(SUM(e.amount), 0) AS total_expenses,
            COALESCE(SUM(i.amount), 0) - COALESCE(SUM(e.amount), 0) AS net_income
        FROM `{PROJECT_ID}.{DATASET}.properties` p
        LEFT JOIN `{PROJECT_ID}.{DATASET}.income` i ON p.property_id = i.property_id
        LEFT JOIN `{PROJECT_ID}.{DATASET}.expenses` e ON p.property_id = e.property_id
        WHERE p.property_id = {property_id}
        GROUP BY p.property_id, p.name, p.address
    """
    try:
        rows = list(bq.query(query).result())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database query failed: {str(e)}")
    return dict(rows[0])








