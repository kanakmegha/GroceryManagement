import React, { useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';
import { formatInrFromPaise } from '../../shared/format/inr';
import { colors, spacing } from '../../shared/theme/tokens';
import { DEMO_DISABLED_REASONS } from './demoCapabilities';
import { useDemoQuery } from './DemoProvider';
import type {
  DemoAnalyticsResponse,
  DemoDailyPlanResponse,
  DemoDailyRecipe,
  DemoDashboardResponse,
  DemoInventoryResponse,
  DemoMealCategory,
  DemoPurchase,
  DemoPurchasesResponse,
  DemoReceiptsResponse,
  DemoRecipesResponse,
} from './demoTypes';
import { Card, DemoQueryView, DisabledAction, Pill, SectionTitle } from './DemoUi';

// Fixed, allowlisted demo paths (the demo client re-validates every path).
const PATHS = {
  dashboard: '/api/v1/demo/dashboard',
  inventory: '/api/v1/demo/inventory',
  purchases: '/api/v1/demo/purchases',
  receipts: '/api/v1/demo/receipts',
  recipes: '/api/v1/demo/recipes',
  dailyPlan: '/api/v1/demo/daily-plan',
  analytics: '/api/v1/demo/analytics',
} as const;

function Page({
  title,
  subtitle,
  onRefresh,
  refreshing,
  children,
}: {
  title: string;
  subtitle?: string;
  onRefresh?: () => void;
  refreshing?: boolean;
  children: React.ReactNode;
}) {
  return (
    <ScrollView
      contentContainerStyle={styles.page}
      refreshControl={
        onRefresh ? <RefreshControl refreshing={!!refreshing} onRefresh={onRefresh} /> : undefined
      }
    >
      <Text style={styles.h1}>{title}</Text>
      {subtitle ? <Text style={styles.sub}>{subtitle}</Text> : null}
      {children}
    </ScrollView>
  );
}

const fmtQty = (v: number | string) => {
  const n = Number(v);
  return Number.isFinite(n) ? String(Math.round(n * 100) / 100) : String(v);
};
const inr = (p: number) => formatInrFromPaise(p, { omitZeroDecimals: true });

function PurchaseCard({ p }: { p: DemoPurchase }) {
  return (
    <Card>
      <Text style={styles.title}>{p.merchant_name}</Text>
      <Text style={styles.muted}>
        {p.purchased_on} · {p.item_count} items · {p.receipt_category}
      </Text>
      <Text style={styles.amount}>{inr(p.total_amount_paise)}</Text>
      {p.items.map((it) => (
        <View key={it.id} style={styles.row}>
          <Text style={styles.rowMain}>
            {it.display_name} ({fmtQty(it.normalized_qty)} {it.base_unit})
          </Text>
          <Text style={styles.rowEnd}>{inr(it.line_total_paise)}</Text>
        </View>
      ))}
    </Card>
  );
}

function RecipeCard({ r }: { r: DemoDailyRecipe }) {
  const [open, setOpen] = useState(false);
  const missing = r.missing_ingredients.length;
  return (
    <Card>
      <Text style={styles.title} onPress={() => setOpen((o) => !o)} accessibilityRole="button">
        {r.title} {open ? '▾' : '▸'}
      </Text>
      <Text style={styles.muted}>
        {r.meal_category} · {r.prep_minutes + (r.cook_minutes ?? 0)} min · serves {r.servings}
        {r.cuisine ? ` · ${r.cuisine}` : ''}
      </Text>
      <Pill
        label={
          r.availability_bucket === 'can_make_now'
            ? 'Can make now'
            : r.availability_bucket === 'almost_there'
              ? `Almost there (${missing} missing)`
              : 'Explore more'
        }
      />
      {r.description ? <Text style={styles.body}>{r.description}</Text> : null}
      {open ? (
        <View style={{ gap: spacing.xs, marginTop: spacing.sm }}>
          <Text style={styles.subTitle}>Ingredients</Text>
          {[...r.available_ingredients, ...r.partial_ingredients, ...r.missing_ingredients].map(
            (i, idx) => (
              <Text key={`${i.name}-${idx}`} style={styles.body}>
                {i.available ? '✓' : '•'} {i.name} {i.quantity}
              </Text>
            )
          )}
          <Text style={styles.subTitle}>Steps</Text>
          {(r.structured_steps.length
            ? r.structured_steps.map((s) => `${s.title}: ${s.instruction}`)
            : r.instructions
          ).map((s, idx) => (
            <Text key={idx} style={styles.body}>
              {idx + 1}. {s}
            </Text>
          ))}
        </View>
      ) : null}
    </Card>
  );
}

export function DemoDashboardScreen() {
  const q = useDemoQuery<DemoDashboardResponse>(PATHS.dashboard);
  return (
    <Page
      title="Demo Kitchen"
      subtitle="A sample household showing every feature."
      onRefresh={() => void q.refetch()}
      refreshing={q.isRefetching}
    >
      <DemoQueryView
        query={q}
        emptyTitle="Demo unavailable"
        emptyBody="The demo has no data right now."
      >
        {(d) => (
          <View style={{ gap: spacing.md }}>
            <View style={styles.stats}>
              <Card>
                <Text style={styles.amount}>{d.inventory_item_count}</Text>
                <Text style={styles.muted}>Pantry items</Text>
              </Card>
              <Card>
                <Text style={styles.amount}>{d.analytics.purchase_count}</Text>
                <Text style={styles.muted}>Purchases</Text>
              </Card>
              <Card>
                <Text style={styles.amount}>{inr(d.analytics.total_spend_paise)}</Text>
                <Text style={styles.muted}>Total spend</Text>
              </Card>
            </View>
            <SectionTitle>Today&apos;s meal ideas</SectionTitle>
            {d.daily_plan.recipes.slice(0, 4).map((r) => (
              <RecipeCard key={r.id} r={r} />
            ))}
            <SectionTitle>Expiring soon</SectionTitle>
            {d.expiring_soon_lots.length === 0 ? (
              <Text style={styles.muted}>Nothing expiring in the next 7 days.</Text>
            ) : (
              d.expiring_soon_lots.map((l) => (
                <Card key={l.id}>
                  <Text style={styles.title}>{l.item_name}</Text>
                  <Text style={styles.muted}>
                    {fmtQty(l.remaining_qty)} {l.base_unit} · expires {l.expires_on}
                  </Text>
                </Card>
              ))
            )}
            <SectionTitle>Recent purchases</SectionTitle>
            {d.recent_purchases.map((p) => (
              <PurchaseCard key={p.id} p={p} />
            ))}
            <DisabledAction label="Add purchase" reason={DEMO_DISABLED_REASONS.addPurchase} />
          </View>
        )}
      </DemoQueryView>
    </Page>
  );
}

export function DemoInventoryScreen() {
  const q = useDemoQuery<DemoInventoryResponse>(PATHS.inventory);
  return (
    <Page title="Inventory" subtitle="Sample pantry stock" onRefresh={() => void q.refetch()} refreshing={q.isRefetching}>
      <DemoQueryView
        query={q}
        isEmpty={(d) => d.summary.length === 0}
        emptyTitle="Empty pantry"
        emptyBody="The demo pantry has no items."
      >
        {(d) => (
          <View style={{ gap: spacing.sm }}>
            <DisabledAction label="Edit stock" reason={DEMO_DISABLED_REASONS.editInventory} />
            {d.summary.map((i) => (
              <Card key={i.canonical_item_id}>
                <Text style={styles.title}>{i.name}</Text>
                <Text style={styles.muted}>
                  {fmtQty(i.total_remaining_qty)} {i.base_unit} · {i.category} · {i.active_lot_count}{' '}
                  lot(s)
                  {i.earliest_expires_on ? ` · earliest expiry ${i.earliest_expires_on}` : ''}
                </Text>
              </Card>
            ))}
          </View>
        )}
      </DemoQueryView>
    </Page>
  );
}

export function DemoPurchasesScreen() {
  const q = useDemoQuery<DemoPurchasesResponse>(PATHS.purchases);
  return (
    <Page title="Purchases" subtitle="Grocery history" onRefresh={() => void q.refetch()} refreshing={q.isRefetching}>
      <DemoQueryView
        query={q}
        isEmpty={(d) => d.items.length === 0}
        emptyTitle="No purchases"
        emptyBody="The demo has no purchase history."
      >
        {(d) => (
          <View style={{ gap: spacing.sm }}>
            <DisabledAction label="Add purchase" reason={DEMO_DISABLED_REASONS.addPurchase} />
            {d.items.map((p) => (
              <PurchaseCard key={p.id} p={p} />
            ))}
          </View>
        )}
      </DemoQueryView>
    </Page>
  );
}

export function DemoReceiptsScreen() {
  const q = useDemoQuery<DemoReceiptsResponse>(PATHS.receipts);
  return (
    <Page title="Receipts" subtitle="Scanned receipt records" onRefresh={() => void q.refetch()} refreshing={q.isRefetching}>
      <DemoQueryView
        query={q}
        isEmpty={(d) => d.items.length === 0}
        emptyTitle="No receipts"
        emptyBody="The demo has no receipts."
      >
        {(d) => (
          <View style={{ gap: spacing.sm }}>
            <DisabledAction label="Scan receipt" reason={DEMO_DISABLED_REASONS.uploadReceipt} />
            {d.items.map((r) => (
              <Card key={r.id}>
                <Text style={styles.title}>{r.merchant_name}</Text>
                <Text style={styles.muted}>
                  {r.purchased_on} · {r.document_type} · {r.item_count} items
                </Text>
                <Text style={styles.amount}>{inr(r.total_amount_paise)}</Text>
                {r.items.map((it) => (
                  <View key={it.id} style={styles.row}>
                    <Text style={styles.rowMain}>{it.display_name}</Text>
                    <Text style={styles.rowEnd}>{inr(it.line_total_paise)}</Text>
                  </View>
                ))}
              </Card>
            ))}
          </View>
        )}
      </DemoQueryView>
    </Page>
  );
}

export function DemoRecipesScreen() {
  const q = useDemoQuery<DemoRecipesResponse>(PATHS.recipes);
  return (
    <Page title="Recipes" subtitle="Ideas from the demo pantry" onRefresh={() => void q.refetch()} refreshing={q.isRefetching}>
      <DemoQueryView
        query={q}
        isEmpty={(d) => d.daily_recipes.length === 0 && d.meal_history.length === 0}
        emptyTitle="No recipes"
        emptyBody="The demo has no recipes."
      >
        {(d) => (
          <View style={{ gap: spacing.sm }}>
            <DisabledAction label="Start cooking" reason={DEMO_DISABLED_REASONS.startCooking} />
            <DisabledAction label="Ask AI Chef" reason={DEMO_DISABLED_REASONS.generateAi} />
            {d.daily_recipes.map((r) => (
              <RecipeCard key={r.id} r={r} />
            ))}
            <SectionTitle>Cooking history</SectionTitle>
            {d.meal_history.map((m) => (
              <Card key={m.id}>
                <Text style={styles.title}>{m.recipe_title}</Text>
                <Text style={styles.muted}>
                  {m.cooked_at.slice(0, 10)} · serves {m.servings}
                  {m.rating ? ` · ${m.rating}★` : ''}
                </Text>
              </Card>
            ))}
          </View>
        )}
      </DemoQueryView>
    </Page>
  );
}

const CATEGORIES: DemoMealCategory[] = ['breakfast', 'lunch', 'snacks', 'dinner'];

export function DemoDailyPlanScreen() {
  const q = useDemoQuery<DemoDailyPlanResponse>(PATHS.dailyPlan);
  return (
    <Page title="Daily Meal Plan" subtitle="A pre-built sample plan (no AI is run)" onRefresh={() => void q.refetch()} refreshing={q.isRefetching}>
      <DemoQueryView
        query={q}
        isEmpty={(d) => d.status === 'empty'}
        emptyTitle="No meal plan"
        emptyBody="The demo has no meal plan."
      >
        {(d) => (
          <View style={{ gap: spacing.sm }}>
            <DisabledAction label="Regenerate plan" reason={DEMO_DISABLED_REASONS.generateAi} />
            {CATEGORIES.map((c) => (
              <View key={c} style={{ gap: spacing.sm }}>
                <SectionTitle>{c.charAt(0).toUpperCase() + c.slice(1)}</SectionTitle>
                {(d.categories[c] ?? []).map((r) => (
                  <RecipeCard key={r.id} r={r} />
                ))}
              </View>
            ))}
          </View>
        )}
      </DemoQueryView>
    </Page>
  );
}

export function DemoAnalyticsScreen() {
  const q = useDemoQuery<DemoAnalyticsResponse>(PATHS.analytics);
  return (
    <Page title="Analytics" subtitle="Spending overview" onRefresh={() => void q.refetch()} refreshing={q.isRefetching}>
      <DemoQueryView
        query={q}
        isEmpty={(d) => d.purchase_count === 0}
        emptyTitle="No analytics yet"
        emptyBody="The demo has no spending data."
      >
        {(d) => (
          <View style={{ gap: spacing.sm }}>
            <View style={styles.stats}>
              <Card>
                <Text style={styles.amount}>{inr(d.total_spend_paise)}</Text>
                <Text style={styles.muted}>Total</Text>
              </Card>
              <Card>
                <Text style={styles.amount}>{inr(d.grocery_spend_paise)}</Text>
                <Text style={styles.muted}>Groceries</Text>
              </Card>
              <Card>
                <Text style={styles.amount}>{inr(d.outside_food_spend_paise)}</Text>
                <Text style={styles.muted}>Eating out</Text>
              </Card>
            </View>
            <SectionTitle>By category</SectionTitle>
            {d.category_breakdown.map((c) => (
              <View key={c.category} style={styles.row}>
                <Text style={styles.rowMain}>{c.category}</Text>
                <Text style={styles.rowEnd}>{inr(c.total_paise)}</Text>
              </View>
            ))}
            <SectionTitle>By merchant</SectionTitle>
            {d.merchant_breakdown.map((m) => (
              <View key={m.merchant_name} style={styles.row}>
                <Text style={styles.rowMain}>{m.merchant_name}</Text>
                <Text style={styles.rowEnd}>{inr(m.total_paise)}</Text>
              </View>
            ))}
            <SectionTitle>Monthly trend</SectionTitle>
            {d.monthly_trend.map((m) => (
              <View key={`${m.year}-${m.month}`} style={styles.row}>
                <Text style={styles.rowMain}>
                  {m.year}-{String(m.month).padStart(2, '0')}
                </Text>
                <Text style={styles.rowEnd}>{inr(m.total_paise)}</Text>
              </View>
            ))}
            <SectionTitle>Budgets</SectionTitle>
            {d.budgets.map((b) => (
              <View key={b.id} style={styles.row}>
                <Text style={styles.rowMain}>{b.period_type}</Text>
                <Text style={styles.rowEnd}>{inr(b.overall_limit_paise)}</Text>
              </View>
            ))}
          </View>
        )}
      </DemoQueryView>
    </Page>
  );
}

const styles = StyleSheet.create({
  page: { padding: spacing.lg, gap: spacing.sm, width: '100%', maxWidth: 960, alignSelf: 'center' },
  h1: { fontSize: 24, fontWeight: '800', color: colors.textPrimary },
  sub: { fontSize: 14, color: colors.textSecondary, marginBottom: spacing.sm },
  title: { fontSize: 16, fontWeight: '700', color: colors.textPrimary },
  subTitle: { fontSize: 14, fontWeight: '700', color: colors.textPrimary, marginTop: spacing.sm },
  muted: { fontSize: 13, color: colors.textMuted },
  body: { fontSize: 14, color: colors.textSecondary, lineHeight: 20 },
  amount: { fontSize: 20, fontWeight: '800', color: colors.primary },
  stats: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  row: { flexDirection: 'row', justifyContent: 'space-between', gap: spacing.sm, paddingVertical: 2 },
  rowMain: { flex: 1, fontSize: 14, color: colors.textPrimary },
  rowEnd: { fontSize: 14, fontWeight: '600', color: colors.textSecondary },
});
