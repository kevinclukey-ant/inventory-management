<template>
  <div class="restocking">
    <div class="page-header">
      <h2>{{ t('restocking.title') }}</h2>
      <p>{{ t('restocking.description') }}</p>
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="error" class="error">{{ error }}</div>
    <div v-else>
      <div class="card">
        <div class="card-header">
          <h3 class="card-title">{{ t('restocking.budget') }}</h3>
        </div>
        <div class="budget-row">
          <input
            type="range"
            class="budget-slider"
            min="0"
            max="50000"
            step="500"
            v-model.number="budget"
          >
          <input
            type="number"
            class="budget-input"
            min="0"
            max="50000"
            step="500"
            v-model.number="budget"
            @change="clampBudget"
          >
        </div>
        <div class="budget-value">{{ formatCurrency(budget, currentCurrency) }}</div>
        <div class="budget-note">{{ t('restocking.budgetNote') }}</div>
      </div>

      <div class="stats-grid">
        <div class="stat-card info">
          <div class="stat-label">{{ t('restocking.budget') }}</div>
          <div class="stat-value">{{ formatCurrency(budget, currentCurrency) }}</div>
        </div>
        <div class="stat-card success">
          <div class="stat-label">{{ t('restocking.allocated') }}</div>
          <div class="stat-value">{{ formatCurrency(allocatedTotal, currentCurrency) }}</div>
        </div>
        <div class="stat-card" :class="remainingBudget < 0 ? 'danger' : 'warning'">
          <div class="stat-label">{{ t('restocking.remaining') }}</div>
          <div class="stat-value">{{ formatCurrency(remainingBudget, currentCurrency) }}</div>
        </div>
      </div>

      <div class="card">
        <div class="card-header">
          <h3 class="card-title">{{ t('restocking.recommendations') }} ({{ recommendations.length }})</h3>
        </div>
        <div class="table-container">
          <table class="restock-table">
            <thead>
              <tr>
                <th>{{ t('restocking.table.sku') }}</th>
                <th>{{ t('restocking.table.itemName') }}</th>
                <th>{{ t('restocking.table.category') }}</th>
                <th>{{ t('restocking.table.warehouse') }}</th>
                <th>{{ t('restocking.table.trend') }}</th>
                <th class="num">{{ t('restocking.table.onHand') }}</th>
                <th class="num">{{ t('restocking.table.forecast') }}</th>
                <th class="num">{{ t('restocking.table.gap') }}</th>
                <th class="num">{{ t('restocking.table.unitCost') }}</th>
                <th class="num">{{ t('restocking.table.quantity') }}</th>
                <th class="num">{{ t('restocking.table.lineTotal') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="recommendations.length === 0">
                <td colspan="11" class="empty-row">{{ t('restocking.noRecommendations') }}</td>
              </tr>
              <tr v-for="row in recommendations" :key="row.sku">
                <td><strong>{{ row.sku }}</strong></td>
                <td>{{ translateProductName(row.name) }}</td>
                <td>{{ row.category }}</td>
                <td>{{ translateWarehouse(row.warehouse) }}</td>
                <td>
                  <span :class="['badge', row.trend]">{{ t('trends.' + row.trend) }}</span>
                </td>
                <td class="num">{{ row.on_hand }}</td>
                <td class="num">{{ row.forecast }}</td>
                <td class="num"><strong>{{ row.gap }}</strong></td>
                <td class="num">{{ formatCurrencyWithDecimals(row.unit_cost, currentCurrency, 2) }}</td>
                <td class="num">
                  <input
                    type="number"
                    class="qty-input"
                    min="0"
                    :max="row.gap"
                    v-model.number="quantities[row.sku]"
                  >
                </td>
                <td class="num">{{ formatCurrency(lineTotal(row), currentCurrency) }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="order-footer">
          <div>
            <div v-if="isOverBudget" class="footer-error">{{ t('restocking.overBudget') }}</div>
            <div v-else-if="submitError" class="footer-error">{{ submitError }}</div>
            <div v-if="placedOrder" class="footer-success">
              {{ t('restocking.orderPlaced', { orderNumber: placedOrder.order_number, date: formatDate(placedOrder.expected_delivery) }) }}
              <router-link to="/orders" class="footer-link">{{ t('restocking.viewOrders') }}</router-link>
            </div>
          </div>
          <button class="place-order-btn" :disabled="!canPlaceOrder" @click="placeOrder">
            {{ submitting ? t('restocking.placingOrder') : t('restocking.placeOrder') }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import { ref, computed, watch, onMounted } from 'vue'
import { api } from '../api'
import { useFilters } from '../composables/useFilters'
import { useI18n } from '../composables/useI18n'
import { formatCurrency, formatCurrencyWithDecimals } from '../utils/currency'

export default {
  name: 'Restocking',
  setup() {
    const { t, currentCurrency, currentLocale, translateProductName, translateWarehouse } = useI18n()
    const { selectedLocation, selectedCategory, getCurrentFilters } = useFilters()

    const loading = ref(true)
    const error = ref(null)
    const forecasts = ref([])
    const inventoryItems = ref([])
    const budget = ref(10000) // always USD; unit_cost is USD
    const quantities = ref({}) // sku -> editable order qty
    const submitting = ref(false)
    const placedOrder = ref(null)
    const submitError = ref(null)

    const loadData = async () => {
      try {
        loading.value = true
        error.value = null
        const filters = getCurrentFilters()

        const [forecastsData, inventoryData] = await Promise.all([
          api.getDemandForecasts(),
          api.getInventory({
            warehouse: filters.warehouse,
            category: filters.category
          })
        ])

        forecasts.value = forecastsData
        inventoryItems.value = inventoryData
      } catch (err) {
        error.value = 'Failed to load restocking data: ' + err.message
      } finally {
        loading.value = false
      }
    }

    // Join forecasts to inventory by SKU; only items whose forecast exceeds stock qualify
    const candidates = computed(() => {
      const bySku = new Map(inventoryItems.value.map(i => [i.sku, i]))
      return forecasts.value.flatMap(f => {
        const inv = bySku.get(f.item_sku)
        if (!inv) return []
        const gap = f.forecasted_demand - inv.quantity_on_hand
        if (gap <= 0) return []
        return [{ sku: inv.sku, name: inv.name, category: inv.category, warehouse: inv.warehouse,
                  trend: f.trend, on_hand: inv.quantity_on_hand, forecast: f.forecasted_demand,
                  gap, unit_cost: inv.unit_cost }]
      }).sort((a, b) => b.gap - a.gap || a.unit_cost - b.unit_cost) // largest gap first, cheaper wins ties
    })

    // Greedy fill: largest gap first, buy as much of the gap as the remaining budget allows.
    // Unaffordable rows stay in the list with 0 so the user can still hand-edit them.
    const recommendations = computed(() => {
      let remaining = budget.value
      return candidates.value.map(c => {
        const affordable = Math.floor(remaining / c.unit_cost)
        const qty = Math.max(0, Math.min(c.gap, affordable))
        remaining -= qty * c.unit_cost
        return { ...c, recommended_qty: qty }
      })
    })

    // Editable quantities live in a separate ref so typing does not fight the computed.
    // They intentionally reset whenever recommendations change (slider or filter move).
    watch(recommendations, recs => {
      quantities.value = Object.fromEntries(recs.map(r => [r.sku, r.recommended_qty]))
    }, { immediate: true })

    // Coerce user input: NaN/negative/fractional -> safe non-negative integer
    const qtyOf = sku => Math.max(0, Math.floor(Number(quantities.value[sku]) || 0))
    const lineTotal = row => qtyOf(row.sku) * row.unit_cost
    const allocatedTotal = computed(() => recommendations.value.reduce((s, r) => s + lineTotal(r), 0))
    const remainingBudget = computed(() => budget.value - allocatedTotal.value)
    const hasItems = computed(() => recommendations.value.some(r => qtyOf(r.sku) > 0))
    // Half-cent tolerance matches the server check so rounding never trips a 400
    const isOverBudget = computed(() => allocatedTotal.value > budget.value + 0.005)
    const canPlaceOrder = computed(() => hasItems.value && !isOverBudget.value && !submitting.value)

    const clampBudget = () => {
      const n = Number(budget.value)
      budget.value = Math.min(50000, Math.max(0, isNaN(n) ? 0 : n))
    }

    const placeOrder = async () => {
      submitting.value = true
      submitError.value = null
      placedOrder.value = null
      try {
        const items = recommendations.value.filter(r => qtyOf(r.sku) > 0)
          .map(r => ({ sku: r.sku, quantity: qtyOf(r.sku) }))
        placedOrder.value = await api.createRestockOrder({ budget: budget.value, items })
      } catch (err) {
        submitError.value = err.response?.data?.detail || err.message
      } finally {
        submitting.value = false
      }
    }

    const formatDate = (dateString) => {
      const locale = currentLocale.value === 'ja' ? 'ja-JP' : 'en-US'
      const date = new Date(dateString)
      if (isNaN(date.getTime())) return dateString
      return date.toLocaleDateString(locale, {
        year: 'numeric',
        month: 'short',
        day: 'numeric'
      })
    }

    watch([selectedLocation, selectedCategory], loadData)
    onMounted(loadData)

    return {
      t,
      currentCurrency,
      translateProductName,
      translateWarehouse,
      loading,
      error,
      budget,
      quantities,
      submitting,
      placedOrder,
      submitError,
      recommendations,
      lineTotal,
      allocatedTotal,
      remainingBudget,
      isOverBudget,
      canPlaceOrder,
      clampBudget,
      placeOrder,
      formatDate,
      formatCurrency,
      formatCurrencyWithDecimals
    }
  }
}
</script>

<style scoped>
.budget-row {
  display: flex;
  gap: 1rem;
  align-items: center;
}

.budget-slider {
  flex: 1;
  accent-color: #3b82f6;
}

.budget-input,
.qty-input {
  padding: 0.4rem 0.6rem;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  font-size: 0.9rem;
}

.budget-input:focus,
.qty-input:focus {
  outline: none;
  border-color: #3b82f6;
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.1);
}

.budget-input {
  width: 130px;
}

.qty-input {
  width: 80px;
  text-align: right;
}

.budget-value {
  font-size: 1.75rem;
  font-weight: 700;
  color: #0f172a;
  margin-top: 0.75rem;
}

.budget-note {
  font-size: 0.8rem;
  color: #64748b;
  margin-top: 0.25rem;
}

.order-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 1rem;
  padding: 1rem 0 0;
  flex-wrap: wrap;
}

.footer-error {
  color: #dc2626;
  font-size: 0.9rem;
}

.footer-success {
  color: #15803d;
  font-size: 0.9rem;
}

.footer-link {
  margin-left: 0.5rem;
  color: #3b82f6;
  font-weight: 600;
}

.place-order-btn {
  padding: 0.75rem 1.5rem;
  background: #3b82f6;
  color: #fff;
  border: none;
  border-radius: 8px;
  font-weight: 600;
  cursor: pointer;
}

.place-order-btn:hover:not(:disabled) {
  background: #2563eb;
}

.place-order-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.empty-row {
  text-align: center;
  color: #64748b;
  padding: 2rem;
}

.num {
  text-align: right;
}
</style>
