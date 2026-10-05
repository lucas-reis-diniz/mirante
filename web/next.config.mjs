/** @type {import('next').NextConfig} */
const nextConfig = {
  // O driver pg é nativo: precisa ficar fora do bundle do servidor.
  serverExternalPackages: ["pg"],
  // A interface é só-leitura e sempre dinâmica — nada de cache de página
  // servindo número velho como se fosse atual.
  poweredByHeader: false,
};

export default nextConfig;
