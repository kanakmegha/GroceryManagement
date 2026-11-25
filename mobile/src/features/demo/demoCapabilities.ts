/** Capability restrictions for Demo Mode. Every mutating / paid capability is false. */
export interface DemoCapabilities {
  readonly canMutate: false;
  readonly canAddPurchase: false;
  readonly canUploadReceipt: false;
  readonly canEditInventory: false;
  readonly canGenerateAi: false;
  readonly canStartCooking: false;
}

export const DEMO_CAPABILITIES: DemoCapabilities = Object.freeze({
  canMutate: false,
  canAddPurchase: false,
  canUploadReceipt: false,
  canEditInventory: false,
  canGenerateAi: false,
  canStartCooking: false,
});

export const DEMO_DISABLED_REASONS = {
  addPurchase: 'Adding purchases is disabled in Demo Mode. Sign in to track your own groceries.',
  uploadReceipt: 'Receipt scanning is disabled in Demo Mode. Sign in to scan your own receipts.',
  editInventory: 'Editing inventory is disabled in Demo Mode. Sign in to manage your own pantry.',
  generateAi:
    'AI meal generation is disabled in Demo Mode. This plan is a pre-built sample. Sign in for personalised plans.',
  startCooking: 'Cooking sessions change stock, so they are disabled in Demo Mode.',
} as const;
