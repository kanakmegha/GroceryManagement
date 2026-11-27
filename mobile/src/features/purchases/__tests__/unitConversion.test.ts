import {
  convertQuantityAndUnitPrice,
  formatPurchaseQuantityDisplay,
} from "../unitConversion";

describe("convertQuantityAndUnitPrice", () => {
  it("converts 2 kg x 53/kg = 106 to 2000 g x 0.053/g = 106", () => {
    const res = convertQuantityAndUnitPrice({
      qtyText: "2",
      unitPriceRupeesText: "53.00",
      lineTotalRupeesText: "106.00",
      fromUnit: "kg",
      toUnit: "g",
    });
    expect(res.qtyText).toBe("2000");
    expect(res.unit).toBe("g");
    expect(res.unitPriceRupeesText).toBe("0.053");
    expect(res.lineTotalRupeesText).toBe("106.00");
  });

  it("converts 2000 g x 0.053/g = 106 back to 2 kg x 53/kg = 106", () => {
    const res = convertQuantityAndUnitPrice({
      qtyText: "2000",
      unitPriceRupeesText: "0.053",
      lineTotalRupeesText: "106.00",
      fromUnit: "g",
      toUnit: "kg",
    });
    expect(res.qtyText).toBe("2");
    expect(res.unit).toBe("kg");
    expect(res.unitPriceRupeesText).toBe("53");
    expect(res.lineTotalRupeesText).toBe("106.00");
  });

  it("converts 1 L x 40/L = 40 to 1000 ml x 0.04/ml = 40", () => {
    const res = convertQuantityAndUnitPrice({
      qtyText: "1",
      unitPriceRupeesText: "40.00",
      lineTotalRupeesText: "40.00",
      fromUnit: "l",
      toUnit: "ml",
    });
    expect(res.qtyText).toBe("1000");
    expect(res.unit).toBe("ml");
    expect(res.unitPriceRupeesText).toBe("0.04");
    expect(res.lineTotalRupeesText).toBe("40.00");
  });

  it("does not corrupt incompatible unit switches (kg -> pcs)", () => {
    const res = convertQuantityAndUnitPrice({
      qtyText: "2",
      unitPriceRupeesText: "53.00",
      lineTotalRupeesText: "106.00",
      fromUnit: "kg",
      toUnit: "pcs",
    });
    expect(res.qtyText).toBe("2");
    expect(res.unit).toBe("pcs");
    expect(res.unitPriceRupeesText).toBe("53.00");
    expect(res.lineTotalRupeesText).toBe("106.00");
  });

  it("does not introduce rounding drift across repeated kg <-> g switches", () => {
    let state = {
      qtyText: "2",
      unit: "kg" as const,
      unitPriceRupeesText: "53.00",
      lineTotalRupeesText: "106.00",
    };
    for (let i = 0; i < 5; i += 1) {
      const toG = convertQuantityAndUnitPrice({
        ...state,
        fromUnit: "kg",
        toUnit: "g",
      });
      expect(toG.qtyText).toBe("2000");
      expect(toG.unitPriceRupeesText).toBe("0.053");
      expect(toG.lineTotalRupeesText).toBe("106.00");

      const backToKg = convertQuantityAndUnitPrice({
        ...toG,
        fromUnit: "g",
        toUnit: "kg",
      });
      expect(backToKg.qtyText).toBe("2");
      expect(backToKg.unitPriceRupeesText).toBe("53");
      expect(backToKg.lineTotalRupeesText).toBe("106.00");
      state = { ...backToKg, unit: "kg" };
    }
  });
});

describe("formatPurchaseQuantityDisplay", () => {
  it("displays original purchase quantity when present", () => {
    expect(
      formatPurchaseQuantityDisplay({
        purchase_qty: 2,
        purchase_unit: "kg",
        normalized_qty: "2000",
        base_unit: "g",
      }),
    ).toBe("2 kg");
  });

  it("explicitly labels legacy normalized-only records without fabricating purchase unit", () => {
    expect(
      formatPurchaseQuantityDisplay({
        purchase_qty: null,
        purchase_unit: null,
        normalized_qty: "2000",
        base_unit: "g",
      }),
    ).toBe("2000 g (inventory unit)");
  });
});
