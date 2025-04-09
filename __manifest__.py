{
    "name": "Account Move Template",
    "version": "saas~18.2.1.0.0",
    "category": "Accounting/Accounting",
    "summary": "Templates for recurring Journal Entries",
    "author": "Agile Business Group, Aurium Technologies, Vauxoo, ForgeFlow, "
    "Akretion, Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/account-financial-tools",
    "license": "AGPL-3",
    "depends": ["account"],
    "data": [
        "security/ir_rule.xml",
        "security/ir.model.access.csv",
        "wizard/account_move_template_run_view.xml",
        "view/account_move_template.xml",
    ],
    "installable": True,
}
