function formatMoney(cents) {
  return `$${(cents / 100).toFixed(2)}`;
}

function calculateCardFee(subtotal) {
  return Math.round(subtotal * 0.1);
}

document.addEventListener("DOMContentLoaded", () => {
  const orderForm = document.querySelector("[data-order-form]");
  if (!orderForm) {
    return;
  }

  const subtotalNode = document.querySelector("[data-subtotal]");
  const feeNode = document.querySelector("[data-fee]");
  const totalNode = document.querySelector("[data-total]");

  function updateSummary() {
    let subtotal = 0;
    document.querySelectorAll("[data-quantity]").forEach((input) => {
      const row = input.closest("[data-price-cents]");
      const price = Number(row?.dataset.priceCents || 0);
      const quantity = Number(input.value || 0);
      subtotal += price * quantity;
    });

    const paymentMethod =
      document.querySelector("[data-payment-option]:checked")?.value || "cash";
    const fee = paymentMethod === "card" ? calculateCardFee(subtotal) : 0;
    const total = subtotal + fee;

    subtotalNode.textContent = formatMoney(subtotal);
    feeNode.textContent = formatMoney(fee);
    totalNode.textContent = formatMoney(total);
  }

  orderForm.addEventListener("input", updateSummary);
  orderForm.addEventListener("change", updateSummary);
  updateSummary();
});
