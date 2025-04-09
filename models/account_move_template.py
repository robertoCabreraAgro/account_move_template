from odoo import api, fields, models, _
from itertools import chain
import logging

_logger = logging.getLogger(__name__)

class AccountMoveTemplate(models.Model):
    _inherit = 'account.move.template'
    
    # Convertir company_id a many2many para soportar múltiples compañías
    company_ids = fields.Many2many(
        'res.company',
        string='Companies',
        default=lambda self: self.env.company,
    )
    
    # Campo calculado para compatibilidad con vistas existentes
    company_id = fields.Many2one(
        'res.company',
        string='Primary Company',
        compute='_compute_primary_company',
        store=True,
    )
    
    workflow_line_ids = fields.One2many(
        'account.workflow.template.line',
        'template_id',
        string='Used in Workflows'
    )
    
    move_type = fields.Selection([
        ('entry', 'Journal Entry'),
        ('out_invoice', 'Customer Invoice'),
        ('out_refund', 'Customer Credit Note'),
        ('in_invoice', 'Vendor Bill'),
        ('in_refund', 'Vendor Credit Note'),
    ], default='entry', required=True)
    
    partner_id = fields.Many2one(
        'res.partner',
        string='Default Partner',
        help='Default partner for this template'
    )
    
    date = fields.Date(
        string='Default Date',
        help='Default date for entries created from this template'
    )
    
    suitable_journal_ids = fields.Many2many(
        'account.journal',
        string='Suitable Journals',
        domain="[('company_id', 'in', company_ids)]",
        help='Journals that can be used with this template'
    )
    
    @api.depends('company_ids')
    def _compute_primary_company(self):
        """Computes a single company for compatibility with views"""
        for template in self:
            template.company_id = template.company_ids[:1] if template.company_ids else self.env.company
    
    def _compute_workflow_count(self):
        """Compute the number of workflows this template is used in"""
        for template in self:
            template.workflow_count = len(template.workflow_line_ids)
    
    workflow_count = fields.Integer(
        string='# Workflows',
        compute='_compute_workflow_count'
    )
    
    def action_view_workflows(self):
        """View workflows where this template is used"""
        self.ensure_one()
        workflows = self.workflow_line_ids.mapped('workflow_id')
        if not workflows:
            return {
                'type': 'ir.actions.act_window_close'
            }
            
        action = self.env.ref('account_move_workflow.action_account_move_workflow').read()[0]
        
        workflow_ids = workflows.ids
        if len(workflow_ids) == 1:
            action['views'] = [(self.env.ref('account_move_workflow.view_account_move_workflow_form').id, 'form')]
            action['res_id'] = workflow_ids[0]
        else: 
            action['domain'] = [('id', 'in', workflow_ids)]
            
        return action