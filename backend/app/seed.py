from sqlalchemy import or_

from .extensions import db
from .models import FAQ, Agent


def seed_agent(username, password):
    """Create the agent from AGENT_USERNAME / AGENT_PASSWORD if it's missing."""
    if not username or not password or Agent.query.filter_by(username=username).first():
        return
    agent = Agent(username=username)
    agent.set_password(password)
    db.session.add(agent)
    db.session.commit()


# (question, answer, keywords) per module. These are generic SAMPLES of a typical ERP
# Service and Sales module, not the actual Ideal Dynamics screens or menu names -
# replace them with your real help content.
DEFAULT_FAQS = {
    "service": [
        ("How do I log a new service request?",
         "In the Service module, start a new service request (service call), choose the "
         "customer and the equipment or serial number, describe the problem and set the "
         "priority, then save it. The request gets a number you can use to track it, and "
         "its response time follows the customer's contract or SLA if they have one.",
         "service,request,call,log,new,create,raise,fault,breakdown,complaint"),
        ("How do I assign a service request to a technician?",
         "Open the service request and assign a technician (or engineer) and a visit date "
         "and time. Check the technician's schedule first so visits don't overlap. The "
         "technician then sees the job in their list, and the request status changes to "
         "assigned or scheduled.",
         "assign,technician,engineer,schedule,visit,dispatch,appointment"),
        ("How do I check whether equipment is under warranty or a service contract?",
         "Look up the equipment by serial number or open it from the customer's record. It "
         "shows the warranty end date and any active service contract. Work covered by a "
         "warranty or contract is usually not charged, or is charged at contract rates.",
         "warranty,contract,covered,serial,equipment,expiry,check,guarantee"),
        ("How do I set up a service contract and its SLA response times?",
         "Create a service contract for the customer, add the equipment it covers, set the "
         "start and end dates, the billing terms, and the SLA response and resolution "
         "times. New service requests for that equipment pick up the contract and its SLA.",
         "contract,sla,amc,setup,response,resolution,time,renewal,agreement"),
        ("How do I record spare parts used on a job?",
         "On the job card (work order) for the visit, add the parts used with their "
         "quantities. Saving or completing the job card takes the parts out of stock, and "
         "they're added to the service invoice unless the work is covered.",
         "spare,parts,spares,used,job,card,stock,issue,consume"),
        ("How do I close a service request and bill the customer?",
         "Complete the job card with the work done, the technician's time and the parts "
         "used, then close the service request. Create the service invoice from the closed "
         "request. Covered work under warranty or contract is excluded or charged at "
         "contract rates.",
         "close,complete,finish,bill,invoice,charge,job,card,billing"),
        ("How do I set up preventive maintenance visits?",
         "Add a preventive maintenance schedule to the equipment or service contract, for "
         "example every 3 months. The system then creates the planned maintenance jobs "
         "ahead of time so they can be assigned to technicians like any other job.",
         "preventive,preventative,maintenance,ppm,planned,schedule,recurring,routine"),
        ("Which service reports are available?",
         "Common service reports show open service requests by status, priority or "
         "technician, SLA compliance (requests answered and fixed on time), technician "
         "workload, parts used, and contracts about to expire. You can usually filter by "
         "date range and export to Excel or PDF.",
         "report,reports,sla,compliance,technician,workload,analysis,export,dashboard"),
    ],
    "sales": [
        ("How do I create a sales quotation?",
         "In the Sales module, start a new quotation, choose the customer, add the items "
         "with quantities and prices, set how long the quote is valid, then save it. You "
         "can print or email the quotation to the customer from the saved record.",
         "quotation,quote,create,new,estimate,proposal"),
        ("How do I convert a quotation into a sales order?",
         "Open the accepted quotation and use the option to convert or copy it to a sales "
         "order. The customer, items and prices are carried over, so check quantities and "
         "the delivery date before saving. The quotation is then marked as converted.",
         "convert,quotation,quote,sales,order,accepted"),
        ("How do I create a sales invoice?",
         "Invoices are usually created from a sales order or delivery so nothing is billed "
         "twice. Open the sales order, choose to create an invoice, check the lines, taxes "
         "and due date, then post it. Once posted, it updates the customer's balance.",
         "invoice,invoicing,bill,billing,create,post,sales"),
        ("How do I issue a credit note or handle a sales return?",
         "Create a credit note against the original posted invoice rather than editing the "
         "invoice. Choose the lines and quantities being returned or credited, give a "
         "reason, and post it. If goods come back, record the return so stock is updated.",
         "credit,note,return,refund,cancel,invoice,reverse"),
        ("How do discounts and price lists work?",
         "Prices come from the price list linked to the customer, so customers or groups "
         "can have their own prices. You can also add a line or document discount on a "
         "quotation, order or invoice, if your user role allows it.",
         "discount,price,list,pricing,special,rate"),
        ("Why can't I save an order because the customer is over their credit limit?",
         "Each customer can have a credit limit. If their unpaid balance plus the new order "
         "is over it, the order is blocked or needs approval. Ask finance to review the "
         "account, record pending payments, or approve the order.",
         "credit,limit,blocked,customer,approval,exceeded,hold"),
        ("Which sales reports are available?",
         "Common sales reports show sales by customer, item, salesperson or period, open "
         "quotations, open sales orders, and outstanding invoices. You can usually filter "
         "by date range and export to Excel or PDF.",
         "report,reports,analysis,sales,salesperson,export,summary"),
        ("How do I change or cancel a sales order?",
         "You can edit a sales order until it has been delivered or invoiced. After that, "
         "cancel the remaining lines, or reverse the delivery or invoice first. Cancelling "
         "keeps the order on record for audit.",
         "cancel,change,edit,amend,sales,order,modify"),
    ],
}


# Default articles from the earlier online-store version of the Service assistant.
# They're removed on startup (only exact, unedited copies), so the ERP Service
# articles replace them. Articles an agent added are never touched.
LEGACY_STORE_ARTICLES = {
    "How do I track my order?",
    "How long does shipping take?",
    "What is your refund policy?",
    "How do I reset my password?",
    "Which payment methods do you accept?",
    "How do I change my delivery address?",
}


def _remove_legacy_store_articles():
    old = FAQ.query.filter(FAQ.question.in_(LEGACY_STORE_ARTICLES),
                           or_(FAQ.module == "service", FAQ.module.is_(None))).all()
    for faq in old:
        db.session.delete(faq)
    if old:
        db.session.commit()


def _has_articles(module):
    q = FAQ.query
    if module == "service":  # rows from before the module column count as service
        return q.filter(or_(FAQ.module == "service", FAQ.module.is_(None))).first() is not None
    return q.filter_by(module=module).first() is not None


def seed_faqs():
    """Add the default articles for any module that has none yet."""
    _remove_legacy_store_articles()
    added = False
    for module, rows in DEFAULT_FAQS.items():
        if not _has_articles(module):
            db.session.add_all(FAQ(question=q, answer=a, keywords=k, module=module)
                               for q, a, k in rows)
            added = True
    if added:
        db.session.commit()
