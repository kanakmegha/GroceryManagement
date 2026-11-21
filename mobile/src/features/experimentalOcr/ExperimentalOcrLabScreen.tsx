import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { router } from 'expo-router';
import * as ImagePicker from 'expo-image-picker';
import * as DocumentPicker from 'expo-document-picker';
import { useApiClient } from '../auth/useApiClient';
import { ApiClientError } from '../../shared/api/client';
import { ScreenContainer } from '../../shared/components';
import { colors, radii, spacing } from '../../shared/theme/tokens';
import { buildReceiptFormData, type ReceiptAssetInput } from '../purchases/receiptUpload';
import type {
  ExperimentalJobOut,
  ExtractModeType,
  ProvenanceType,
} from './types';

function ProvenanceBadge({ provenance }: { provenance: ProvenanceType }) {
  const bg =
    provenance === 'OBSERVED'
      ? '#dcfce7'
      : provenance === 'DERIVED'
        ? '#fef9c3'
        : provenance === 'INFERRED'
          ? '#ffedd5'
          : '#f1f5f9';
  return (
    <View style={[styles.badge, { backgroundColor: bg }]}>
      <Text style={styles.badgeText}>{provenance}</Text>
    </View>
  );
}

export function ExperimentalOcrLabScreen() {
  const apiClient = useApiClient();
  const [selectedAsset, setSelectedAsset] = useState<ReceiptAssetInput | null>(null);
  const [mode, setMode] = useState<ExtractModeType>('compare');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [job, setJob] = useState<ExperimentalJobOut | null>(null);
  const [healthText, setHealthText] = useState<string>('Checking experimental service...');

  // Separate manual ground-truth entry (never mixed with engine predictions)
  const [gtMerchant, setGtMerchant] = useState('');
  const [gtDate, setGtDate] = useState('');
  const [gtGrandTotal, setGtGrandTotal] = useState('');
  const [gtSavedMsg, setGtSavedMsg] = useState<string | null>(null);

  useEffect(() => {
    apiClient
      .get<{
        enabled: boolean;
        provider: string;
        model: string;
        ppocrv6_available: boolean;
      }>('/api/experimental/receipts/health')
      .then((h) => {
        setHealthText(
          `${h.provider} (${h.model}) · ${h.enabled ? 'ENABLED' : 'DISABLED'} · PP-OCRv6: ${
            h.ppocrv6_available ? 'ready' : 'unavailable'
          }`
        );
      })
      .catch(() => setHealthText('Experimental OCR health check failed'));
  }, [apiClient]);

  const handleCapture = useCallback(async () => {
    setErrorMsg(null);
    const perm = await ImagePicker.requestCameraPermissionsAsync();
    if (!perm.granted) {
      setErrorMsg('Camera permission is required to capture a receipt.');
      return;
    }
    const res = await ImagePicker.launchCameraAsync({ mediaTypes: ['images'], quality: 0.85 });
    if (!res.canceled && res.assets?.[0]) {
      const a = res.assets[0];
      setSelectedAsset({
        uri: a.uri,
        name: a.fileName ?? `exp-receipt-${Date.now()}.jpg`,
        mimeType: a.mimeType ?? 'image/jpeg',
      });
    }
  }, []);

  const handlePickFile = useCallback(async () => {
    setErrorMsg(null);
    const res = await DocumentPicker.getDocumentAsync({
      type: ['image/*', 'application/pdf'],
      multiple: false,
      copyToCacheDirectory: true,
    });
    if (!res.canceled && res.assets?.[0]) {
      const a = res.assets[0];
      setSelectedAsset({
        uri: a.uri,
        name: a.name ?? `exp-receipt-${Date.now()}.jpg`,
        mimeType: a.mimeType ?? 'image/jpeg',
      });
    }
  }, []);

  const handleSubmit = useCallback(async () => {
    if (!selectedAsset || isSubmitting) return;
    setIsSubmitting(true);
    setErrorMsg(null);
    setGtSavedMsg(null);
    try {
      const formData = await buildReceiptFormData(selectedAsset);
      formData.append('mode', mode);
      const result = await apiClient.post<ExperimentalJobOut>(
        '/api/experimental/receipts/extract',
        formData
      );
      setJob(result);
    } catch (err) {
      setErrorMsg(
        err instanceof ApiClientError || err instanceof Error
          ? err.message
          : 'Experimental extraction failed.'
      );
    } finally {
      setIsSubmitting(false);
    }
  }, [apiClient, isSubmitting, mode, selectedAsset]);

  const handleSaveGroundTruth = useCallback(async () => {
    if (!job) return;
    try {
      const updated = await apiClient.post<ExperimentalJobOut>(
        `/api/experimental/receipts/jobs/${job.job_id}/ground-truth`,
        {
          merchant_name: gtMerchant.trim() || null,
          purchase_date: gtDate.trim() || null,
          grand_total: gtGrandTotal.trim() ? Number(gtGrandTotal) : null,
        }
      );
      setJob(updated);
      setGtSavedMsg('Ground truth saved separately (predictions & inventory untouched).');
    } catch {
      setGtSavedMsg('Failed to save ground truth.');
    }
  }, [apiClient, gtDate, gtGrandTotal, gtMerchant, job]);

  const exp = job?.experimental_result;
  const ex = job?.existing_result;

  return (
    <ScreenContainer>
      <ScrollView contentContainerStyle={styles.container}>
        <View style={styles.headerRow}>
          <Pressable onPress={() => router.back()} style={styles.backBtn}>
            <Text style={styles.backText}>Back</Text>
          </Pressable>
          <Text style={styles.title}>Experimental OCR Lab</Text>
        </View>
        <Text style={styles.subtitle}>
          Isolated V10.6 Sandbox · Zero Inventory / Learning Writes
        </Text>
        <Text style={styles.healthText}>{healthText}</Text>

        <View style={styles.buttonRow}>
          <Pressable style={styles.actionBtn} onPress={handleCapture}>
            <Text style={styles.actionBtnText}>Capture Receipt</Text>
          </Pressable>
          <Pressable style={styles.actionBtn} onPress={handlePickFile}>
            <Text style={styles.actionBtnText}>Select Image / PDF</Text>
          </Pressable>
        </View>

        {selectedAsset && (
          <Text style={styles.selectedText}>
            Selected: {selectedAsset.name} ({selectedAsset.mimeType})
          </Text>
        )}

        <View style={styles.modeRow}>
          {(['existing', 'experimental', 'compare'] as ExtractModeType[]).map((m) => (
            <Pressable
              key={m}
              onPress={() => setMode(m)}
              style={[styles.modeChip, mode === m && styles.modeChipActive]}
            >
              <Text style={[styles.modeChipText, mode === m && styles.modeChipTextActive]}>
                {m === 'existing' ? 'Existing OCR' : m === 'experimental' ? 'Experimental V10.6' : 'Compare Both'}
              </Text>
            </Pressable>
          ))}
        </View>

        <Pressable
          style={[styles.submitBtn, (!selectedAsset || isSubmitting) && styles.submitDisabled]}
          onPress={handleSubmit}
          disabled={!selectedAsset || isSubmitting}
        >
          {isSubmitting ? (
            <ActivityIndicator color="#fff" />
          ) : (
            <Text style={styles.submitText}>Run Isolated Extraction</Text>
          )}
        </Pressable>

        {errorMsg && <Text style={styles.errorText}>{errorMsg}</Text>}

        {job && (
          <View style={styles.card}>
            <Text style={styles.cardTitle}>Job: {job.job_id}</Text>
            <Text style={styles.metaText}>
              Mode: {job.mode} · Inventory Mutated: {String(job.inventory_mutated)}
            </Text>

            {/* Header Comparison */}
            <Text style={styles.sectionHeader}>Header & Totals Comparison</Text>
            <View style={styles.compRow}>
              <Text style={styles.compLabel}>Merchant</Text>
              <Text style={styles.compVal}>Existing: {ex?.merchant_name ?? '—'}</Text>
              <View style={styles.compRight}>
                <Text style={styles.compVal}>
                  Exp: {exp?.merchant_evidence.observed_merchant ?? exp?.merchant_evidence.inferred_merchant ?? 'Unavailable'}
                </Text>
                {exp && <ProvenanceBadge provenance={exp.merchant_evidence.provenance} />}
              </View>
            </View>
            <View style={styles.compRow}>
              <Text style={styles.compLabel}>Purchase Date</Text>
              <Text style={styles.compVal}>Existing: {ex?.receipt_date ?? '—'}</Text>
              <View style={styles.compRight}>
                <Text style={styles.compVal}>
                  Exp: {exp?.date_evidence.parsed_date ?? exp?.date_evidence.printed_date ?? '—'} ({exp?.date_evidence.ambiguity_status ?? '—'})
                </Text>
                {exp && <ProvenanceBadge provenance={exp.date_evidence.provenance} />}
              </View>
            </View>
            <View style={styles.compRow}>
              <Text style={styles.compLabel}>Subtotal / Grand Total</Text>
              <Text style={styles.compVal}>
                Existing: {ex?.subtotal_rupees ?? '—'} / {ex?.grand_total_rupees ?? '—'} (lat {ex?.latency_ms ?? 0}ms)
              </Text>
              <View style={styles.compRight}>
                <Text style={styles.compVal}>
                  Exp: {exp?.subtotal_after_tax.value ?? exp?.subtotal_before_tax.value ?? '—'} / {exp?.grand_total.value ?? '—'} (lat {exp?.latency_ms ?? 0}ms)
                </Text>
                {exp && <ProvenanceBadge provenance={exp.grand_total.provenance} />}
              </View>
            </View>

            {exp?.error && <Text style={styles.errorText}>Experimental Error: {exp.error}</Text>}
            {ex?.error && <Text style={styles.errorText}>Existing Error: {ex.error}</Text>}

            {/* Items */}
            <Text style={styles.sectionHeader}>
              Extracted Items (Existing: {ex?.items.length ?? 0} · Experimental: {exp?.items.length ?? 0})
            </Text>
            {exp?.items.map((it) => (
              <View key={it.line_index} style={styles.itemCard}>
                <Text style={styles.itemTitle}>
                  {it.line_index}. {it.product_name.value} ({it.package_size.value ?? 'no pkg'})
                </Text>
                <Text style={styles.metaText}>
                  Qty: {it.quantity.value ?? '—'} {it.unit_of_measure.value ?? ''} · MRP: {it.mrp.value ?? '—'} · Sell: {it.selling_price.value ?? '—'} · Total: {it.line_total.value ?? '—'} · Disc: {it.item_discount.value ?? '—'}
                </Text>
                <View style={styles.badgeRow}>
                  <Text style={styles.metaText}>UnitPrice ({it.unit_price.value ?? '—'}): </Text>
                  <ProvenanceBadge provenance={it.unit_price.provenance} />
                  <Text style={styles.metaText}> GST ({it.gst.observed_tax_total ?? it.gst.derived_tax_total ?? '—'}): </Text>
                  <ProvenanceBadge provenance={it.gst.tax_provenance} />
                </View>
              </View>
            ))}

            {/* Fees */}
            {exp && exp.fees.length > 0 && (
              <>
                <Text style={styles.sectionHeader}>Fees ({exp.fees.length})</Text>
                {exp.fees.map((f, idx) => (
                  <Text key={idx} style={styles.metaText}>
                    • {f.fee_type}: orig={f.original_amount ?? '—'}, actual={f.actual_amount ?? '—'} [{f.charge_status}]
                  </Text>
                ))}
              </>
            )}

            {/* Separate Ground Truth Entry */}
            <Text style={styles.sectionHeader}>Manual Ground Truth (Stored Separately)</Text>
            <TextInput
              style={styles.input}
              placeholder="Verified Merchant Name"
              value={gtMerchant}
              onChangeText={setGtMerchant}
            />
            <TextInput
              style={styles.input}
              placeholder="Verified Date (YYYY-MM-DD)"
              value={gtDate}
              onChangeText={setGtDate}
            />
            <TextInput
              style={styles.input}
              placeholder="Verified Grand Total (INR)"
              value={gtGrandTotal}
              onChangeText={setGtGrandTotal}
              keyboardType="decimal-pad"
            />
            <Pressable style={styles.actionBtn} onPress={handleSaveGroundTruth}>
              <Text style={styles.actionBtnText}>Save Ground Truth</Text>
            </Pressable>
            {gtSavedMsg && <Text style={styles.metaText}>{gtSavedMsg}</Text>}
          </View>
        )}
      </ScrollView>
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  container: { padding: spacing.md, gap: spacing.sm },
  headerRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  backBtn: { paddingVertical: 6, paddingHorizontal: 10, borderRadius: radii.sm, backgroundColor: colors.surfaceMuted },
  backText: { color: colors.textPrimary, fontWeight: '600' },
  title: { fontSize: 20, fontWeight: '700', color: colors.textPrimary },
  subtitle: { fontSize: 13, color: colors.textMuted },
  healthText: { fontSize: 12, color: colors.primary, fontWeight: '600' },
  buttonRow: { flexDirection: 'row', gap: spacing.sm },
  actionBtn: { flex: 1, backgroundColor: colors.surfaceMuted, padding: spacing.sm, borderRadius: radii.md, alignItems: 'center' },
  actionBtnText: { color: colors.textPrimary, fontWeight: '600', fontSize: 13 },
  selectedText: { fontSize: 12, color: colors.textMuted },
  modeRow: { flexDirection: 'row', gap: 6 },
  modeChip: { flex: 1, paddingVertical: 8, borderRadius: radii.md, borderWidth: 1, borderColor: colors.border, alignItems: 'center' },
  modeChipActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  modeChipText: { fontSize: 12, fontWeight: '600', color: colors.textPrimary },
  modeChipTextActive: { color: '#fff' },
  submitBtn: { backgroundColor: colors.primary, padding: spacing.md, borderRadius: radii.md, alignItems: 'center' },
  submitDisabled: { opacity: 0.5 },
  submitText: { color: '#fff', fontWeight: '700' },
  errorText: { color: '#dc2626', fontSize: 12, fontWeight: '600' },
  card: { backgroundColor: colors.surface, padding: spacing.md, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.border, gap: 8 },
  cardTitle: { fontSize: 15, fontWeight: '700', color: colors.textPrimary },
  sectionHeader: { fontSize: 14, fontWeight: '700', color: colors.textPrimary, marginTop: 8 },
  compRow: { borderTopWidth: 1, borderTopColor: colors.border, paddingTop: 6, gap: 2 },
  compLabel: { fontSize: 12, fontWeight: '700', color: colors.textPrimary },
  compVal: { fontSize: 12, color: colors.textMuted },
  compRight: { flexDirection: 'row', alignItems: 'center', gap: 6, flexWrap: 'wrap' },
  itemCard: { backgroundColor: colors.surfaceMuted, padding: 8, borderRadius: radii.sm, gap: 4 },
  itemTitle: { fontSize: 13, fontWeight: '600', color: colors.textPrimary },
  badgeRow: { flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', gap: 4 },
  badge: { paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4 },
  badgeText: { fontSize: 10, fontWeight: '700', color: '#1e293b' },
  metaText: { fontSize: 12, color: colors.textMuted },
  input: { borderWidth: 1, borderColor: colors.border, borderRadius: radii.sm, padding: 8, fontSize: 13, color: colors.textPrimary },
});
