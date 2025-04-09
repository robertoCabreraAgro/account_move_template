from odoo import api, fields, models, _
from itertools import chain
import logging

_logger = logging.getLogger(__name__)

class AccountMoveTemplate(models.Model):
    _inherit = 'account.move.template'
    
    # Reemplazamos company_id por company_ids para mantener compatibilidad
    # con la definición original en account_move_template
    company_ids = fields.Many2many(
        'res.company',
        string='Companies',
        default=lambda self: self.env.company,
    )
    
    # Campo calculado para mostrar en la interfaz
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